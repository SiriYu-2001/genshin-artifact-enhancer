"""Background CLI worker. No model or browser automation is used by the UI."""
import json
from pathlib import Path
import sys
import time

from .navigation import ROOT
from .batch import save
from .ui_config import materialize_snapshot,validate


def _main():
    request=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    output=Path(request['output']);kind=request['kind']
    job_started=time.time();data=None
    try:
        if kind=='scan':
            from .workflow import fresh_scan
            scan=fresh_scan(request.get('scope','current'))
            import hashlib
            result={'status':'completed','scan_directory':str(scan),'snapshot_id':hashlib.sha256(str(scan.resolve()).encode()).hexdigest()[:16]}
        elif kind=='resume':
            from .batch import main as batch
            batch()
            state=json.loads((ROOT/'runtime/active-batch.json').read_text(encoding='utf-8'))
            result={'status':state['status'],'directory':state['directory'],
                    'report':json.loads((Path(state['directory'])/'summary.json').read_text(encoding='utf-8'))}
            policy=Path(state['directory'])/'auto-equip-policy.json'
            if policy.exists() and json.loads(policy.read_text(encoding='utf-8')).get('enabled') and state['status'] in ('finished','finished-with-deferred'):
                from .finish_equip import apply_finished
                result['equipment']=apply_finished(state,json.loads(policy.read_text(encoding='utf-8')).get('scene'))
                save(policy,{'enabled':False,'status':'verified'})
        elif kind=='backend-check':
            from .good_backend import ensure_backend,REVISION
            client=ensure_backend()
            result={'status':'completed','backend':'GOODScanner','revision':REVISION,'game_input':False,
                    'health':client.request('GET','/health')}
        elif kind=='reconcile':
            from .good_enhancement import main as reconcile
            result=reconcile(reconcile_only=True)
        elif kind in ('elixir','dust'):
            if kind=='elixir':from .elixir_report import calculate
            else:from .dust_report import calculate
            result=calculate(request['config'],request['snapshot'],request['demand_id'],
                             lambda x:print(json.dumps(x,ensure_ascii=False),flush=True))
        elif kind=='equip':
            from .equip import apply
            s=request['snapshot']
            result=apply(ROOT/'runtime/loadouts/library.json',request['ids'],s['path'],s.get('updates'))
        else:
            config=Path(request['config']);data=validate(json.loads((config/'config.json').read_text(encoding='utf-8')))
            snapshot=request.get('snapshot')
            if kind=='preview':
                from .campaign_runtime import offline
                print(json.dumps({'phase':'planning','demands':len(data['demands'])}),flush=True)
                started=time.perf_counter()
                result=offline(config/'campaign.json',snapshot['path'],snapshot.get('updates'))
                from .telemetry import record
                record('plan','offline-campaign',started)
                result['status']='offline-preview'
                from .campaign_report import write_report
                write_report(result,output.with_suffix('.md'))
            elif kind=='start':
                scan=None
                if snapshot:
                    scan=materialize_snapshot(snapshot['path'],snapshot.get('updates'),output.parent/('scan-'+output.parent.name))
                else:
                    from .workflow import fresh_scan
                    scan=fresh_scan(request.get('scope','current'))
                if data['mode']=='single':
                    from .workflow import start
                    start(config/(data['demands'][0]['id']+'.json'),'borrow' if data['equipment']=='borrow' else 'no-borrow',scan)
                else:
                    from .campaign_runtime import start
                    start(config/'campaign.json',scan)
                state=json.loads((ROOT/'runtime/active-batch.json').read_text(encoding='utf-8'))
                result={'status':state['status'],'directory':state['directory'],
                        'report':json.loads((Path(state['directory'])/'summary.json').read_text(encoding='utf-8'))}
                if data.get('auto_equip_after') and state['status'] in ('finished','finished-with-deferred'):
                    print(json.dumps({'phase':'equipping_final_builds'}),flush=True)
                    from .finish_equip import apply_finished
                    result['equipment']=apply_finished(state,data.get('equip_scene'))
            else:raise ValueError('Unknown UI operation')
        save(output,result)
    except Exception as exc:
        if kind in ('start','resume'):
            manifest=ROOT/'runtime/active-batch.json'
            if manifest.exists():
                state=json.loads(manifest.read_text(encoding='utf-8'))
                if state.get('status') in ('running','prepared'):
                    state['status']='stopped' if (ROOT/'runtime/stop.signal').exists() else 'needs-attention'
                    save(manifest,state)
                if kind=='start' and data and data.get('auto_equip_after') and state.get('started',0)>=job_started:
                    save(Path(state['directory'])/'auto-equip-policy.json',{'enabled':True,'scene':data.get('equip_scene')})
        save(output,{'status':'failed','error':str(exc)})
        raise


def main():
    request=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'))
    if request['kind'] not in ('scan','start','resume','equip','backend-check','reconcile'):
        return _main()
    from .game_lease import GameLease
    try:
        with GameLease():
            from .good_backend import prepare_game_job
            prepare_game_job(ROOT)
            return _main()
    except Exception as exc:
        output=Path(request['output'])
        if not output.exists():save(output,{'status':'failed','error':str(exc)})
        raise


if __name__=='__main__':main()
