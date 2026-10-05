"""Isolated ZIP acceptance through the frozen app's public HTTP scan action.

The harness is external; the tested process has no Python/Agent/dev paths in its
environment. It does not call controller internals or assist game decisions.
"""
import argparse,hashlib,json,os,subprocess,time,zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import build_opener,ProxyHandler,Request

import ctypes
if os.name=="nt" and not ctypes.windll.shell32.IsUserAnAdmin():
    raise RuntimeError("Run this verification harness from an Administrator terminal; the release EXE requires elevation.")

ROOT=Path(__file__).resolve().parents[1]
p=argparse.ArgumentParser();p.add_argument('--archive',type=Path,required=True);args=p.parse_args()
archive=args.archive.resolve();folder=ROOT/'runtime/release-isolation'/datetime.now().strftime('%Y%m%d-%H%M%S')
folder.mkdir(parents=True,exist_ok=False)
with zipfile.ZipFile(archive) as z:
    assert not any('runtime' in Path(n).parts or '.git' in Path(n).parts for n in z.namelist())
    z.extractall(folder)
app=folder/'ArtifactWorkbench';assert not (app/'runtime').exists()
manifest=json.loads((app/'build-manifest.json').read_text(encoding='utf-8'))
assert all(hashlib.sha256((app/n).read_bytes()).hexdigest()==digest for n,digest in manifest['files'].items())
env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','COMSPEC','PATHEXT')}
windows=Path(env.get('SystemRoot','C:/Windows'))
env['PATH']=os.pathsep.join(str(v) for v in (windows/'System32',windows/'System32/WindowsPowerShell/v1.0',windows))
env['PYTHONNOUSERSITE']='1'
log=(folder/'launcher.log').open('wb')
proc=subprocess.Popen([str(app/'ArtifactWorkbench.exe'),'--no-browser'],cwd=app,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
opener=build_opener(ProxyHandler({}));url=None;token='';job=None
evidence={'archive':archive.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'fresh_runtime':True,'clean_environment':True,'app':str(app),'pid':proc.pid,'status':'starting'}
def save():
    (folder/'acceptance.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'runtime/release-isolation/latest.json').write_text(json.dumps({'directory':str(folder),'app':str(app)},ensure_ascii=False),encoding='utf-8')
def api(path,body=None):
    req=Request(url+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Local-Token':token})
    with opener.open(req,timeout=30) as r:return json.load(r)
save()
try:
    for _ in range(120):
        try:
            url=json.loads((app/'runtime/ui/server.json').read_text(encoding='utf-8'))['url']
            health=api('/api/health')
            if health['frozen'] and health['instance']==hashlib.sha256(str(app).encode()).hexdigest()[:16]:break
        except (OSError,ValueError,KeyError):pass
        if proc.poll() is not None:raise RuntimeError('Frozen app exited')
        time.sleep(.25)
    else:raise RuntimeError('Frozen app not ready')
    bootstrap=api('/api/bootstrap');token=bootstrap['token']
    assert not bootstrap.get('draft')
    assert not api('/api/catalog')['snapshots']
    evidence.update(url=url,status='scanning');save()
    job=api('/api/jobs',{'kind':'scan'})['id'];evidence['job']=job;save()
    print(json.dumps({'event':'scan_started','url':url,'job':job,'package':archive.name}),flush=True)
    start=time.monotonic();last='';last_progress=0
    while time.monotonic()-start<2700:
        state=api('/api/state');row=state['job']
        if row and row['id']==job and row['status']!='running':break
        # Only display safe phase messages, never raw session files or credentials.
        for line in reversed((row or {}).get('logs',[])[-5:]):
            try:entry=json.loads(line)
            except (TypeError,ValueError):continue
            phase=entry.get('phase')
            if phase and (phase!=last or time.monotonic()-last_progress>45):
                print(json.dumps({'phase':phase,'elapsed':round(time.monotonic()-start),'message':entry.get('message','')},ensure_ascii=True),flush=True)
                last=phase;last_progress=time.monotonic()
            if phase:break
        time.sleep(1)
    else:raise RuntimeError('Scan test timed out')
    evidence['job_status']=row['status'];evidence['seconds']=round(time.monotonic()-start,2)
    if row['status']!='completed':
        evidence['failure']=row.get('result');raise RuntimeError(json.dumps(row.get('result'),ensure_ascii=True))
    snapshots=api('/api/catalog')['snapshots'];assert len(snapshots)==1
    snapshot=snapshots[0];scan=Path(snapshot['path']) if 'path' in snapshot else None
    # Public catalog hides paths: use only this isolated app's output receipt.
    output=json.loads((app/'runtime/ui/jobs'/job/'result.json').read_text(encoding='utf-8'))
    scan=Path(output['scan_directory']);assert scan.is_relative_to(app/'runtime')
    raw=json.loads((scan/'enhancer-artifacts.json').read_text(encoding='utf-8-sig'))
    count=json.loads((scan/'scan-count.json').read_text(encoding='utf-8-sig'))['requested']
    assert len(raw)==count and {x['index'] for x in raw}==set(range(1,count+1))
    coverage=json.loads((scan/'coverage.json').read_text(encoding='utf-8'))
    assert coverage['complete'] and coverage['termination']=='Exhausted'
    assert coverage['visited']==coverage['expected']==coverage['five_star']+coverage['skipped_lower_rarity']
    assert coverage['accepted']==coverage['five_star']==count and not coverage['unknown_rarity'] and not coverage['missed']
    backend=json.loads((app/'runtime/goodscanner/server.json').read_text(encoding='utf-8'))
    assert Path(backend['exe_path']).resolve()==app/'bin/goodscanner/workbench_goodscanner.exe'
    assert backend['exe_hash']==manifest['files']['bin/goodscanner/workbench_goodscanner.exe']
    assert not (app/'runtime/session.json').exists() and not (app/'runtime/yas-service').exists()
    evidence.update(status='passed',recognized=count,five_star=count,coverage=coverage,
                    complete_positions=True,goodscanner_from_package=True,consuming_actions=0)
    print(json.dumps({k:v for k,v in evidence.items() if k not in ('app','navigation_actions')},ensure_ascii=True),flush=True)
except Exception as exc:
    evidence.update(status='failed',error=str(exc));print(json.dumps({'status':'failed','error':str(exc)},ensure_ascii=True),flush=True)
    raise
finally:
    save()
    # No untracked long-lived controller; stop only this isolated installation.
    (app/'runtime/stop.signal').touch()
    if proc.poll() is None:proc.terminate();proc.wait(timeout=15)
    log.close()
