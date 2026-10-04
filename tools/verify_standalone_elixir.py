"""Autonomous portability test: clean environment, copied app, local HTTP only.

No Agent SDK, computer-use, game process, OCR model or admin bridge is required.
Keeps private evidence in the selected output and temporary workspace. Does not
modify the source configuration or inventory. Run from any ordinary terminal.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import Request,urlopen,build_opener,ProxyHandler


ROOT=Path(__file__).resolve().parents[1]


def clean_environment():
    keep=('SystemRoot','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA',
          'PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMDATA','COMSPEC','PATHEXT')
    env={k:v for k,v in os.environ.items() if k.upper() in {x.upper() for x in keep}}
    env['PATH']=os.pathsep.join((str(Path(sys.executable).parent),str(Path(os.environ.get('SystemRoot','C:/Windows'))/'System32')))
    env.update(PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1',PYTHONNOUSERSITE='1')
    return env


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--scan',type=Path,required=True)
    parser.add_argument('--updates',type=Path)
    parser.add_argument('--demand',required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--kind',choices=['elixir','dust'],default='elixir')
    parser.add_argument('--compare-dust',action='store_true')
    parser.add_argument('--target-score',type=float)
    parser.add_argument('--longterm-days',type=int)
    parser.add_argument('--daily-resin',type=int,default=180)
    parser.add_argument('--longterm-samples',type=int,choices=(128,512,2048),default=128)
    args=parser.parse_args()
    work=Path(tempfile.mkdtemp(prefix='artifact-standalone-'))
    app=work/'app';app.mkdir();guard=work/'guard';guard.mkdir()
    report={'status':'running','workspace':str(work),'started':time.time(),'checks':[],'python':sys.executable}
    inputs=[args.config,args.scan/'enhancer-artifacts.json',args.scan/'scan-count.json']
    if args.updates:inputs.append(args.updates)
    original_hashes={str(p.resolve()):digest(p) for p in inputs}
    process=None;stream=None
    try:
        for name,glob in [('enhancer','*.py'),('data','*.json'),('profiles','*.json'),('web','*')]:
            target=app/name;target.mkdir()
            for p in (ROOT/name).glob(glob):
                if p.is_file():shutil.copy2(p,target/p.name)
        scan=app/'runtime/controller-standalone'/args.scan.name;scan.mkdir(parents=True)
        for name in ('enhancer-artifacts.json','scan-count.json','ownership-overrides.json'):
            src=args.scan/name
            if src.exists():shutil.copy2(src,scan/name)
        campaign=app/'runtime/standalone-campaign';campaign.mkdir()
        if args.updates:shutil.copy2(args.updates,campaign/'inventory-updates.json')
        write(app/'runtime/active-batch.json',{'status':'finished','directory':str(campaign),'scan_directory':str(scan)})
        assert not (app/'runtime/session.json').exists()
        assert not (app/'vendor').exists() and not (app/'.tools').exists()
        audit=work/'guard-events.jsonl'
        blocked=[str(ROOT.resolve()),str(Path.home()/'.codex'),str(Path.home()/'AppData/Local/OpenAI/Codex')]
        guard_code='''import sys,os,json,time,subprocess
from pathlib import Path
LOG=Path(os.environ['STANDALONE_AUDIT_LOG'])
BLOCKED=[os.path.normcase(os.path.abspath(p)) for p in json.loads(os.environ['STANDALONE_BLOCKED_PATHS'])]
def log(kind,**data):
    with LOG.open('a',encoding='utf-8') as f:f.write(json.dumps(dict(kind=kind,pid=os.getpid(),at=time.time(),**data))+'\\n')
def deny(kind,**data):
    log(kind,**data);raise PermissionError('Standalone guard: '+kind)
def path_check(path):
    if isinstance(path,(str,bytes,os.PathLike)):
        p=os.path.normcase(os.path.abspath(os.fsdecode(path)))
        if any(p==b or p.startswith(b+os.sep) for b in BLOCKED):deny('denied_path',path=p)
def audit(event,args):
    if event in ('open','os.listdir','os.scandir') and args:path_check(args[0])
    elif event in ('socket.connect','socket.getaddrinfo'):
        address=args[1] if event=='socket.connect' else args[0]
        host=address[0] if isinstance(address,tuple) else address
        if host not in ('127.0.0.1','::1','localhost'):deny('denied_network',host=str(host))
        log('loopback_network',host=str(host))
    elif event=='import' and args[0].split('.')[0] in ('openai','anthropic','codex','agents','langchain','playwright','selenium','pyautogui'):
        deny('denied_agent_import',module=args[0])
    elif event=='subprocess.Popen':
        executable,argv=args[:2]
        # Windows audit events contain a command-line string and executable=None.
        # requests/platform may also query the OS version; permit exactly `ver`.
        version=str(Path(os.environ['SystemRoot'])/'system32/cmd.exe')+' /c "ver"'
        if isinstance(argv,str) and os.path.normcase(argv)==os.path.normcase(version):
            log('system_version_probe');return
        prefix=subprocess.list2cmdline([sys.executable,'-u','-m','enhancer.ui_worker'])
        allowed=(isinstance(argv,str) and argv.startswith(prefix+' ')) or (isinstance(argv,(list,tuple)) and list(argv[:4])==[sys.executable,'-u','-m','enhancer.ui_worker'])
        if not allowed:
            deny('denied_process',executable=str(executable),command=str(argv))
        log('worker_spawn',executable=sys.executable)
    elif event=='ctypes.dlsym' and str(args[1]) in ('ShellExecuteW','ShellExecuteExW'):
        deny('denied_elevation')
sys.addaudithook(audit)
log('guard_started',argv=sys.argv,agent_env_present=any(k.upper().startswith(('CODEX','OPENAI','ANTHROPIC','AGENT_')) for k in os.environ))
'''
        (guard/'sitecustomize.py').write_text(guard_code,encoding='utf-8')
        env=clean_environment();env.update(PYTHONPATH=str(guard),STANDALONE_AUDIT_LOG=str(audit),STANDALONE_BLOCKED_PATHS=json.dumps(blocked))
        # Negative probes prove the guard is loaded, rather than assuming it is.
        probe="""import socket,os,json
blocked=0
for f in (lambda:open(json.loads(os.environ['STANDALONE_BLOCKED_PATHS'])[0]+'/AGENTS.md'),lambda:socket.create_connection(('8.8.8.8',443),timeout=.1),lambda:__import__('openai')):
 try:f()
 except PermissionError:blocked+=1
assert blocked==3,blocked
print('Three forbidden operations blocked')
"""
        p=subprocess.run([sys.executable,'-c',probe],cwd=app,env=env,capture_output=True,text=True,encoding='utf-8',timeout=20)
        if p.returncode:raise RuntimeError(p.stderr or p.stdout)
        report['checks'].append('guard_negative_probes_passed')
        with socket.socket() as available:available.bind(('127.0.0.1',0));port=available.getsockname()[1]
        base=f'http://127.0.0.1:{port}';opener=build_opener(ProxyHandler({}));token=None
        def api(path,data=None):
            headers={'Content-Type':'application/json'}
            if token:headers['X-Local-Token']=token
            request=Request(base+path,data=None if data is None else json.dumps(data).encode(),headers=headers)
            with opener.open(request,timeout=10) as r:return json.loads(r.read())
        def start(number):
            nonlocal process,stream,token
            stream=(work/f'server-{number}.log').open('wb')
            process=subprocess.Popen([sys.executable,'-u','-m','enhancer','ui','--port',str(port)],cwd=app,env=env,
                                      stdout=stream,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            until=time.monotonic()+25
            while time.monotonic()<until:
                if process.poll() is not None:raise RuntimeError('Standalone server exited: '+(work/f'server-{number}.log').read_text(encoding='utf-8',errors='replace'))
                try:bootstrap=api('/api/bootstrap');token=bootstrap['token'];return bootstrap
                except OSError:time.sleep(.2)
            raise TimeoutError('Standalone server startup')
        def stop():
            nonlocal process,stream
            if process and process.poll() is None:process.terminate();process.wait(timeout=10)
            if stream:stream.close()
            process=None;stream=None
        bootstrap=start(1)
        for path in ('/','/app.js','/dust.js','/longterm.js','/style.css'):
            with opener.open(base+path,timeout=10) as response:
                assert response.status==200 and "default-src 'self'" in response.headers['Content-Security-Policy']
                assert len(response.read())>100
        config=json.loads(args.config.read_text(encoding='utf-8-sig'))
        assert any(d['id']==args.demand for d in config['demands'])
        if args.kind=='elixir':config['elixir']={'budget':4,'remaining_by_set':{},'minimum_gain':0.,'objective':'expected_gain','respect_priority':True}
        else:config['dust']={'budget':33,'points':2,'phase':2,'objective':'efficiency','respect_priority':True,'metadata':{}}
        if args.compare_dust:
            config['elixir']['compare_dust']=True
            config['dust']={'budget':33,'points':2,'phase':2,'objective':'efficiency','respect_priority':True,'metadata':{}}
        if args.target_score is not None:
            next(d for d in config['demands'] if d['id']==args.demand)['profile']['resource_target_score']=args.target_score
        if args.longterm_days is not None:
            config[args.kind]['longterm']={'enabled':True,'days':args.longterm_days,'daily_resin':args.daily_resin,'samples':args.longterm_samples}
        saved=api('/api/config',{'config':config});identifier=saved['id']
        api('/api/draft',{'client':'standalone-test','sequence':1,'payload':{'config':saved['config'],'config_id':identifier,'active_index':0}})
        snapshot=next(s for s in api('/api/catalog')['snapshots'] if s['label']==args.scan.name)
        report['checks']+=['clean_app_without_ocr_or_controller','assets_served_with_local_only_csp','config_and_draft_saved_through_http']
        def run(number):
            job=api('/api/jobs',{'kind':args.kind,'config_id':identifier,'demand_id':args.demand,'snapshot_id':snapshot['id']})['id']
            until=time.monotonic()+600
            while time.monotonic()<until:
                state=api('/api/state');j=state['job']
                if j and j['id']==job and j['status']!='running':break
                time.sleep(.5)
            else:raise TimeoutError('Standalone calculation')
            if j['status']!='completed':raise RuntimeError(json.dumps(j,ensure_ascii=False))
            result=json.loads((app/'runtime/ui/jobs'/job/'result.json').read_text(encoding='utf-8'))
            if args.kind=='elixir':assert result['p_four']=='1/3' and result['p_three']=='2/3'
            else:assert result['current_transitions']['2']=={'guarantee':2,'points':4,'phase':2,'triggered':False}
            if args.compare_dust:assert result['resource_comparison']['dust']
            if args.target_score is not None:assert 'target_gain' in result['rankings']
            if args.longterm_days is not None:
                assert result['longterm']['horizons'][-1]['days']==args.longterm_days
                assert result['longterm']['shortlist_count']>0
            for plans in result['rankings'].values():
                assert plans and all(p['cost']<=4 and (args.kind=='dust' or p['definitions']<=2) for p in plans)
            assert api('/api/'+args.kind+'/latest')['job_id']==job
            digest_result=hashlib.sha256(json.dumps(result,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
            record={'pass':number,'seconds':round(j['finished']-j['started'],2),'job':job,'result_sha256':digest_result,
                    'character':result['character'],'kind':args.kind}
            report.setdefault('passes',[]).append(record);print(json.dumps(record,ensure_ascii=False),flush=True)
            return result
        first=run(1);stop();start(2)
        assert api('/api/config/'+identifier)[args.kind]==saved['config'][args.kind]
        assert api('/api/draft/latest')['payload']['config'][args.kind]==saved['config'][args.kind]
        assert api('/api/'+args.kind+'/latest')['character']==first['character']
        report['checks'].append('restart_restored_sqlite_config_draft_and_results')
        second=run(2);assert first==second,'Deterministic results changed across cold start'
        stop()
        events=[json.loads(line) for line in audit.read_text(encoding='utf-8').splitlines()]
        assert sum(e['kind']=='worker_spawn' for e in events)==2
        assert not any(e.get('agent_env_present') for e in events)
        assert sum(e['kind'].startswith('denied_') for e in events)==3,'Unexpected blocked dependency in actual app'
        assert {str(p.resolve()):digest(p) for p in inputs}==original_hashes
        assert not (app/'runtime/session.json').exists()
        report['checks']+=['identical_results_across_cold_starts','only_two_python_workers_spawned','no_agent_env_or_sdk_access',
                           'no_nonlocal_network_or_original_workspace_access','original_inventory_and_config_unchanged','no_controller_session_created']
        report.update(status='passed',finished=time.time(),audit=str(audit),input_sha256=original_hashes)
    except Exception as exc:
        report.update(status='failed',error=str(exc),finished=time.time());raise
    finally:
        if process and process.poll() is None:process.terminate();process.wait(timeout=10)
        if stream:stream.close()
        write(args.output,report)
    print(json.dumps({'status':report['status'],'checks':report['checks'],'workspace':str(work)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
