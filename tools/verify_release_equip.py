"""Frozen ZIP equip acceptance; only selected user data is imported, never old code or sessions."""
import argparse,copy,hashlib,json,os,subprocess,time,zipfile
from datetime import datetime
from pathlib import Path
from urllib.request import build_opener,ProxyHandler,Request

import ctypes
if os.name=="nt" and not ctypes.windll.shell32.IsUserAnAdmin():
    raise RuntimeError("Run this verification harness from an Administrator terminal; the release EXE requires elevation.")

ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--archive',type=Path,required=True)
parser.add_argument('--library',type=Path,required=True)
parser.add_argument('--scan',type=Path,required=True)
parser.add_argument('--id',required=True)
parser.add_argument('--alternate-index',type=int,required=True)
args=parser.parse_args();archive=args.archive.resolve()
folder=ROOT/'runtime/release-isolation'/('equip-'+datetime.now().strftime('%Y%m%d-%H%M%S'));folder.mkdir(parents=True)
with zipfile.ZipFile(archive) as z:z.extractall(folder)
app=folder/'ArtifactWorkbench';assert not (app/'runtime').exists()
manifest=json.loads((app/'build-manifest.json').read_text(encoding='utf-8'))
assert all(hashlib.sha256((app/n).read_bytes()).hexdigest()==v for n,v in manifest['files'].items())
runtime=app/'runtime';scan=runtime/'controller-import/job-import';scan.mkdir(parents=True)
raw=json.loads((args.scan/'enhancer-artifacts.json').read_text(encoding='utf-8-sig'))
(scan/'enhancer-artifacts.json').write_text(json.dumps(raw,ensure_ascii=False),encoding='utf-8')
(scan/'scan-count.json').write_bytes((args.scan/'scan-count.json').read_bytes())
library=json.loads(args.library.read_text(encoding='utf-8-sig'))
saved=copy.deepcopy(library['loadouts'][args.id][-1]);saved['source_allocation']='User-provided loadout data'
alternate=copy.deepcopy(saved);alternate['name']='临时换装验收（随后恢复保存方案）';alternate['equipment_policy']='protected'
piece=next(x for x in raw if x['index']==args.alternate_index)
assert piece['rarity']==5 and piece['level']==20 and piece['slotKey']=='flower' and piece['equip_raw'].strip() in ('','源','来源')
attributes={'set_key':piece['setKey'],'slot':piece['slotKey'],'rarity':5,'level':20,'main':piece['mainStatKey'],
            'substats':sorted([{'key':s['key'],'value':round(float(s['value']),1 if s['key'].endswith('_') else 0),'pending':s.get('pending',False)} for s in piece['substats']],key=lambda s:s['key'])}
ref={'attributes':attributes,'fingerprint':hashlib.sha256(json.dumps(attributes,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
     'name':piece['name'],'source_id':'job-import:'+str(piece['index']),'owner_hint':''}
alternate['items']=[ref if x['attributes']['slot']=='flower' else x for x in alternate['items']]
assert next(x['fingerprint'] for x in saved['items'] if x['attributes']['slot']=='flower')!=ref['fingerprint']
(runtime/'loadouts').mkdir();(runtime/'loadouts/library.json').write_text(json.dumps({'version':1,'loadouts':{'saved':[saved],'alternate':[alternate]}},ensure_ascii=False),encoding='utf-8')
env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','COMSPEC','PATHEXT')}
windows=Path(env.get('SystemRoot','C:/Windows'));env['PATH']=os.pathsep.join(str(v) for v in (windows/'System32',windows/'System32/WindowsPowerShell/v1.0',windows));env['PYTHONNOUSERSITE']='1'
log=(folder/'launcher.log').open('wb');proc=subprocess.Popen([str(app/'ArtifactWorkbench.exe'),'--no-browser'],cwd=app,env=env,stdout=log,stderr=subprocess.STDOUT,creationflags=subprocess.CREATE_NO_WINDOW)
opener=build_opener(ProxyHandler({}));url=None;token=''
evidence={'archive':archive.name,'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'app':str(app),'status':'starting',
          'import':'user loadout and historical inventory only; no prior runtime, session or code','jobs':[]}
def save():
    (folder/'acceptance.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'runtime/release-isolation/latest-equip.json').write_text(json.dumps({'directory':str(folder),'app':str(app)},ensure_ascii=False),encoding='utf-8')
def api(path,body=None):
    request=Request(url+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Local-Token':token})
    with opener.open(request,timeout=30) as r:return json.load(r)
save()
try:
    for _ in range(120):
        try:
            url=json.loads((runtime/'ui/server.json').read_text(encoding='utf-8'))['url'];health=api('/api/health')
            if health['frozen'] and health['instance']==hashlib.sha256(str(app).encode()).hexdigest()[:16]:break
        except (OSError,ValueError,KeyError):pass
        time.sleep(.25)
    else:raise RuntimeError('Frozen app did not start')
    token=api('/api/bootstrap')['token'];snapshot=api('/api/catalog')['snapshots'][0]
    evidence.update(url=url,status='running');save()
    for identifier in ('alternate','saved','saved'):
        job=api('/api/jobs',{'kind':'equip','ids':[identifier],'snapshot_id':snapshot['id']})['id']
        print(json.dumps({'phase':'equip_test','loadout':identifier,'job':job}),flush=True)
        begin=time.monotonic();last='';last_notice=0
        while time.monotonic()-begin<1200:
            state=api('/api/state')['job']
            if state and state['id']==job and state['status']!='running':break
            for line in reversed((state or {}).get('logs',[])[-6:]):
                try:v=json.loads(line)
                except (TypeError,ValueError):continue
                compact={k:v[k] for k in ('phase','preflight','slot','status','message') if k in v}
                if compact:
                    msg=json.dumps(compact,ensure_ascii=True)
                    if msg!=last or time.monotonic()-last_notice>45:print(msg,flush=True);last=msg;last_notice=time.monotonic()
                    break
            time.sleep(1)
        if state['status']!='completed':raise RuntimeError(json.dumps(state.get('result'),ensure_ascii=True))
        result=json.loads((runtime/'ui/jobs'/job/'result.json').read_text(encoding='utf-8'))
        assert result['status']=='verified'
        changes=sum(x['status']=='equipped' for row in result['loadouts'] for x in row['items'])
        verified=sum(len(row['verified']) for row in result['loadouts']);assert verified==5
        evidence['jobs'].append({'loadout':identifier,'status':'verified','changes':changes,'verified_slots':verified,'seconds':round(time.monotonic()-begin,2)})
        save();print(json.dumps(evidence['jobs'][-1]),flush=True)
    assert evidence['jobs'][0]['changes']>=1,'No actual equipment change was tested'
    assert evidence['jobs'][1]['changes']>=1,'Restoration did not change the temporary flower'
    assert evidence['jobs'][2]['changes']==0,'Repeated loadout should not trigger additional equipment changes'
    backend=json.loads((runtime/'goodscanner/server.json').read_text(encoding='utf-8'))
    assert Path(backend['exe_path']).resolve()==app/'bin/goodscanner/workbench_goodscanner.exe'
    assert backend['exe_hash']==manifest['files']['bin/goodscanner/workbench_goodscanner.exe']
    assert not (runtime/'session.json').exists() and not (runtime/'yas-service').exists()
    evidence.update(status='passed',goodscanner_from_package=True,consuming_actions=0,restored_saved_loadout=True)
    print(json.dumps({k:v for k,v in evidence.items() if k!='app'},ensure_ascii=True),flush=True)
except Exception as exc:
    evidence.update(status='failed',error=str(exc));print(json.dumps({'status':'failed','error':str(exc)},ensure_ascii=True),flush=True);raise
finally:
    save();(runtime/'stop.signal').touch()
    proc.terminate();proc.wait(timeout=15);log.close()
