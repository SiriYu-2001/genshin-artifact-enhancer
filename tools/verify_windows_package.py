"""Exercise the frozen app over HTTP. Optional real yas scan; never enhance/equip."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from urllib.request import build_opener,ProxyHandler,Request


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--app',type=Path,required=True)
    p.add_argument('--config',type=Path,required=True)
    p.add_argument('--live-scan',action='store_true')
    args=p.parse_args();app=args.app.resolve();runtime=app/'runtime';runtime.mkdir(exist_ok=True)
    env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','COMSPEC','PATHEXT')}
    windows=Path(env.get('SystemRoot','C:/Windows'))
    env['PATH']=os.pathsep.join(str(x) for x in (windows/'System32',windows/'System32/WindowsPowerShell/v1.0',windows))
    process=subprocess.Popen([str(app/'ArtifactWorkbench.exe'),'--no-browser'],cwd=app,env=env,
                             stdout=(runtime/'validation-stdout.log').open('wb'),stderr=subprocess.STDOUT,
                             creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    opener=build_opener(ProxyHandler({}));token=None;url=None
    def api(path,body=None):
        request=Request(url+path,data=None if body is None else json.dumps(body).encode(),
                        headers={'Content-Type':'application/json','X-Local-Token':token or ''})
        with opener.open(request,timeout=30) as r:return json.load(r)
    until=time.monotonic()+40
    while time.monotonic()<until:
        try:
            server=json.loads((runtime/'ui/server.json').read_text(encoding='utf-8'));url=server['url']
            health=api('/api/health')
            if health['frozen'] and health['instance']==hashlib.sha256(str(app).encode()).hexdigest()[:16]:break
        except (OSError,ValueError,KeyError):pass
        time.sleep(.3)
    else:raise RuntimeError('Packaged server did not start; inspect validation-stdout.log')
    token=api('/api/bootstrap')['token']
    evidence={'exe':str(app/'ArtifactWorkbench.exe'),'url':url,'frozen':True,'python_on_path':False,'agent_environment':False,'jobs':[]}
    def job(kind,**extra):
        identifier=api('/api/jobs',{'kind':kind,**extra})['id'];start=time.monotonic();last_notice=0
        while time.monotonic()-start<2700:
            state=api('/api/state');current=state['job']
            if current and current['id']==identifier and current['status']!='running':break
            if time.monotonic()-last_notice>30:
                print(json.dumps({'kind':kind,'status':'running','seconds':round(time.monotonic()-start)},ensure_ascii=True),flush=True);last_notice=time.monotonic()
            time.sleep(1)
        else:raise RuntimeError('Job deadline exceeded')
        row={'kind':kind,'id':identifier,'status':current['status'],'seconds':round(time.monotonic()-start,2),'result':current.get('result')}
        evidence['jobs'].append(row)
        (runtime/'package-validation.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
        if current['status']!='completed':raise RuntimeError(json.dumps(row,ensure_ascii=True))
        print(json.dumps({'kind':kind,'status':'passed','seconds':row['seconds']},ensure_ascii=True),flush=True)
        return row['result']
    # Copy only a configuration explicitly supplied by the operator, never credentials.
    config=json.loads(args.config.read_text(encoding='utf-8-sig'));config['auto_equip_after']=False
    saved=api('/api/config',{'config':config});cid=saved['id']
    if args.live_scan:job('scan')
    snapshots=api('/api/catalog')['snapshots']
    if not snapshots:raise RuntimeError('No inventory to test; request --live-scan or import a local snapshot')
    snapshot=snapshots[0];evidence['scan_count']=snapshot['count'];evidence['five_star']=snapshot['five_star']
    job('preview',config_id=cid,snapshot_id=snapshot['id'])
    # Bound the advice test; independent from the user's original saved form.
    for kind in ('elixir','dust'):
        config.setdefault(kind,{})['longterm']={'enabled':True,'days':30,'daily_resin':180,'samples':128}
    config['elixir'].update(budget=4,compare_dust=False)
    config['dust']['budget']=33
    cid=api('/api/config',{'id':cid,'config':config})['id']
    for kind in ('elixir','dust'):
        result=job(kind,config_id=cid,snapshot_id=snapshot['id'],demand_id=config['demands'][0]['id'])
        assert result.get('longterm',{}).get('horizons'), 'Long-term forecast missing from public API'
        assert api('/api/'+kind+'/latest')['longterm']['method']=='paired_future_inventory_mc_v1'
    evidence['status']='passed';evidence['game_consumption']=0
    (runtime/'package-validation.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':'passed','url':url,'count':evidence['scan_count'],'five_star':evidence['five_star'],'report':str(runtime/'package-validation.json')},ensure_ascii=True),flush=True)


if __name__=='__main__':main()
