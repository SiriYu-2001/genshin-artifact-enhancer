"""Loopback-only local control panel with one background operation at a time."""
from collections import deque
from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import urlparse
import uuid

from .navigation import ROOT
from .batch import save
from .ui_config import compile_config,MAINS
from .model import MEANS,ROLLS
from .sets import SET_LABELS

ASSETS=Path(__file__).resolve().parents[1]/'web'


def read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (OSError,ValueError):return default


def public_report(data):
    if not data:return None
    if data.get('kind')=='dust':
        return {k:data[k] for k in ('kind','status','character','demand_name','set_label','options','baseline','reserved_count',
            'crit_cap','current_transitions','inventory','deferred','scope','rankings','candidate_count','deferred_count','message','target_score','longterm') if k in data}
    if data.get('kind')=='elixir':
        return {k:data[k] for k in ('kind','status','character','demand_name','demand_id','set_label','options',
            'remaining','baseline','reserved_count','p_four','p_three','scope','configuration','message','rankings','resource_comparison','longterm') if k in data}
    if 'report' in data:
        result=public_report(data['report']);result['status']=data.get('status',result.get('status'))
        if data.get('equipment'):result['equipment']=public_report(data['equipment']).get('equipment',[])
        return result
    keys=('status','scores','items','confirmations','conflicts','shared_items','transfers','blocked_demands','error','scan_directory')
    result={k:data[k] for k in keys if k in data}
    if 'loadouts' in data:
        result['equipment']=[{'id':r['id'],'character':r['character'],'verified_slots':len(r.get('verified',[])),
                              'changed':sum(a.get('status')=='equipped' for a in r.get('items',[])),
                              'items':[{'name':a.get('name'),'slot':a.get('slot'),'status':a.get('status')} for a in r.get('items',[])]}
                             for r in data['loadouts']]
    if 'demands' in data:
        result['demands']=[{k:r[k] for k in ('id','name','character','status','score','before','gain','independent_score','items','eligible_count') if k in r}
                           | {'deferred_count':len(r.get('deferred',[]))} for r in data['demands']]
    result['remaining_count']=len(data.get('remaining_eligible',[]))
    result['deferred_count']=len(data.get('deferred',[]))
    return result


class Backend:
    def __init__(self,root=ROOT):
        self.root=Path(root);self.runtime=self.root/'runtime';self.directory=self.runtime/'ui'
        self.directory.mkdir(parents=True,exist_ok=True)
        from .ui_storage import LocalStore
        self.store=LocalStore(self.directory/'workbench.sqlite3')
        for p in (self.directory/'configs').glob('*/config.json'):
            data=read(p)
            if data and not self.store.is_deleted(p.parent.name) and self.store.config(p.parent.name) is None:self.store.save_config(p.parent.name,data)
        self.token=secrets.token_urlsafe(32);self.lock=threading.Lock();self.job=None;self.process=None
        self.snapshots={};self.jobs={};self._catalog_cache=None;self._catalog_time=0
        self.cleanup_previews={}
        self.import_previews={}
        previous=sorted((self.directory/'jobs').glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True)
        if previous:
            job=read(previous[0])
            if job and job.get('status')!='running':
                log=previous[0].parent/'stdout.log'
                lines=log.read_text(encoding='utf-8',errors='replace').splitlines()[-100:] if log.exists() else []
                job['logs']=deque(lines,maxlen=200);self.job=job;self.jobs[job['id']]=job

    def config_path(self,identifier):
        if not isinstance(identifier,str) or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):
            raise ValueError('配置ID无效')
        path=self.directory/'configs'/identifier
        data=self.store.config(identifier)
        if data is None:raise ValueError('配置不存在')
        if not (path/'config.json').exists():compile_config(data,path)
        return path

    def configurations(self):
        return [{'id':r['id'],'name':r['config']['name'],'mode':r['config']['mode'],'count':len(r['config']['demands'])}
                for r in self.store.configurations()]

    def save_config(self,payload):
        identifier=payload.get('id') or uuid.uuid4().hex
        if payload.get('id'):self.config_path(identifier)
        data=compile_config(payload['config'],self.directory/'configs'/identifier)
        revision=self.store.save_config(identifier,data)
        self._catalog_cache=None
        return {'id':identifier,'config':data,'revision':revision}

    def storage(self):
        from .storage_management import inspect
        result=inspect(self.runtime)
        result.pop('files');result.pop('fingerprint')
        return {**result,'directory':str(self.runtime.resolve()),'configs':self.store.configurations(),
                'trash':self.store.trash(),'history_limit':20}

    def import_inventory(self,payload):
        from .import_inventory import parse_document,commit_import
        with self.lock:
            if self.process and self.process.poll() is None:raise ValueError('任务运行中不能导入库存，请先停止或等待完成')
            self.import_previews={k:v for k,v in self.import_previews.items() if time.time()-v[0]<600}
            action=payload.get('action')
            if action=='preview':
                rows,summary=parse_document(payload.get('content'))
                token=secrets.token_hex(16)
                if len(self.import_previews)>=3:self.import_previews.pop(next(iter(self.import_previews)))
                self.import_previews[token]=(time.time(),rows,summary,str(payload.get('filename','')))
                return {'preview':token,**summary}
            if action=='commit':
                stored=self.import_previews.pop(payload.get('preview',''),None)
                if not stored:raise ValueError('导入预览已过期，请重新选择文件')
                _,rows,summary,label=stored
                directory,duplicate=commit_import(self.runtime,rows,summary,label)
                self._catalog_cache=None;self.catalog(refresh=True)
                identifier=hashlib.sha256(str(directory.resolve()).encode()).hexdigest()[:16]
                return {'snapshot_id':identifier,'duplicate':duplicate,'summary':summary}
            raise ValueError('未知库存导入操作')

    def manage_presets(self,payload):
        from .ui_config import validate
        if payload.get('action')=='save':
            data=validate({'name':'角色预设','mode':'single','equipment':'borrow','allocation':'priority',
                           'demands':[{'id':'preset','profile':payload.get('profile',{})}],'scenarios':None})
            identifier=self.store.save_preset(data['demands'][0]['profile'])
        elif payload.get('action')=='delete':
            identifier=payload.get('id','')
            if identifier not in {r['id'] for r in self.store.presets()}:raise ValueError('自定义预设不存在；内置示例不能删除')
            self.store.delete_preset(identifier)
        else:raise ValueError('未知预设操作')
        self._catalog_cache=None
        return {'id':identifier,'presets':self.store.presets()}

    def manage_storage(self,payload):
        from .storage_management import inspect,clean,regular_tree
        with self.lock:
            if self.state()['busy']:raise ValueError('任务运行中不能管理数据，请等待完成')
            action=payload.get('action')
            if action in ('archive','restore','purge'):
                identifier=payload.get('id','')
                if not isinstance(identifier,str) or len(identifier)!=32 or any(c not in '0123456789abcdef' for c in identifier):raise ValueError('配置ID无效')
                if action=='purge':
                    if payload.get('confirm')!=identifier:raise ValueError('请确认彻底删除此配置')
                    if not self.store.is_deleted(identifier):raise ValueError('先将配置移入回收站')
                    # Keep the tombstone; otherwise a JSON copy or stale tab can resurrect it.
                    directory=self.directory/'configs'/identifier
                    if directory.exists():
                        paths=list(directory.rglob('*'))
                        if not regular_tree(directory,self.runtime.resolve()) or any(not regular_tree(p,self.runtime.resolve()) for p in paths):raise ValueError('配置目录含链接，拒绝删除')
                        for p in sorted(paths,key=lambda p:len(p.parts),reverse=True):
                            if p.is_file():p.unlink()
                            elif p.is_dir():p.rmdir()
                        directory.rmdir()
                    self.store.purge(identifier)
                elif action=='archive':self.store.archive(identifier)
                else:self.store.restore(identifier)
                self._catalog_cache=None
                return {'ok':True}
            if action=='compact':
                self.store.compact();return {'ok':True}
            if action=='preview':
                result=inspect(self.runtime,0)
                token=secrets.token_hex(16)
                self.cleanup_previews={token:(time.time(),result)}
                return {k:v for k,v in result.items() if k!='files'}|{'preview':token,'examples':[f['path'] for f in result['files'][:12]]}
            if action=='clean':
                stored=self.cleanup_previews.pop(payload.get('preview',''),None)
                if not stored or time.time()-stored[0]>300:raise ValueError('预览已过期，请重新预览')
                return clean(self.runtime,stored[1])
            raise ValueError('未知数据管理操作')

    def catalog(self,refresh=False):
        if not refresh and self._catalog_cache and time.monotonic()-self._catalog_time<15:return self._catalog_cache
        manifests=[read(p) for p in [self.runtime/'active-batch.json',*self.runtime.glob('previous-batch-*.json')]]
        by_scan={}
        paths=set(self.runtime.glob('controller-*/job-*/enhancer-artifacts.json'))|set(self.runtime.glob('imports/import-*/enhancer-artifacts.json'))
        for state in manifests:
            if not state:continue
            scan=Path(state.get('scan_directory',''));updates=Path(state.get('directory',''))/'inventory-updates.json'
            if (scan/'enhancer-artifacts.json').exists():paths.add(scan/'enhancer-artifacts.json')
            if updates.exists():
                previous=by_scan.get(str(scan.resolve()))
                if not previous or updates.stat().st_mtime>previous.stat().st_mtime:by_scan[str(scan.resolve())]=updates
        self.snapshots={}
        equip=read(self.runtime/'active-equip.json',{})
        equip_time=(self.runtime/'active-equip.json').stat().st_mtime if equip.get('status')=='verified' else 0
        for path in sorted(paths,key=lambda p:p.stat().st_mtime,reverse=True):
            count=read(path.parent/'scan-count.json',{}).get('requested')
            raw=read(path,[])
            if not count or len(raw)!=count or {a.get('index') for a in raw}!=set(range(1,count+1)):continue
            identifier=hashlib.sha256(str(path.parent.resolve()).encode()).hexdigest()[:16]
            update=by_scan.get(str(path.parent.resolve()))
            modified=max(path.stat().st_mtime,update.stat().st_mtime if update else 0)
            source=read(path.parent/'snapshot-source.json')
            imported=read(path.parent/'import-info.json')
            self.snapshots[identifier]={'id':identifier,'path':str(path.parent.resolve()),'updates':str(update) if update else None,
                'count':count,'five_star':sum(a.get('rarity')==5 for a in raw),'date':datetime.fromtimestamp(modified).strftime('%m-%d %H:%M'),
                'ownership_stale':bool(equip_time>modified or source),'label':imported['label'] if imported else path.parent.name,'derived':bool(source),
                'source':'import' if imported else 'scan','import_summary':imported['summary'] if imported else None}
        profiles=[{'id':p.stem,'data':read(p)} for p in (self.root/'profiles').glob('*.json')]
        library=read(self.runtime/'loadouts/library.json',{'loadouts':{}})
        saved=[{'id':identifier,'name':revs[-1]['name'],'character':revs[-1]['character'],'revision':revs[-1]['revision'],
                'items':revs[-1]['items']} for identifier,revs in library.get('loadouts',{}).items() if revs]
        from .import_inventory import DATA
        result={'profiles':profiles,'sets':SET_LABELS,'main_options':MAINS,'means':{k:float(v) for k,v in MEANS.items()},
                'characters':sorted(set(DATA['characters'].values())),
                'presets':self.store.presets(),
                'snapshots':[{k:v for k,v in s.items() if k not in ('path','updates')} for s in self.snapshots.values()],
                'loadouts':saved,'configs':self.configurations(),'rolls':{k:[float(v) for v in values] for k,values in ROLLS.items()}}
        self._catalog_cache=result;self._catalog_time=time.monotonic();return result

    def snapshot(self,identifier):
        self.catalog(refresh=True)
        if identifier not in self.snapshots:raise ValueError('请先在“库存导入”页导入 JSON 或扫描库存')
        return self.snapshots[identifier]

    def latest_elixir(self,kind='elixir'):
        for path in sorted((self.directory/'jobs').glob('*/job.json'),key=lambda p:p.stat().st_mtime,reverse=True):
            job=read(path,{})
            if job.get('kind')==kind and job.get('status')=='completed':
                result=public_report(read(path.parent/'result.json'))
                if result:
                    result['job_id']=job['id'];result['calculated_at']=job.get('finished')
                    source=job.get('request',{}).get('snapshot',{})
                    result['snapshot']={k:source[k] for k in ('label','date','ownership_stale') if k in source}
                    return result
        return None

    def state(self):
        job=None
        if self.job:
            job={k:v for k,v in self.job.items() if k not in ('logs','output','request')}
            job['logs']=list(self.job['logs'])[-70:]
            job['result']=public_report(read(self.job['output']))
            if job['result'] and self.job.get('request',{}).get('snapshot'):
                source=self.job['request']['snapshot']
                job['result']['snapshot']={k:source[k] for k in ('label','date','ownership_stale') if k in source}
            if job['status']=='running' and job['kind'] in ('start','resume'):
                active=read(self.runtime/'active-batch.json',{})
                directory=Path(active.get('directory',''))
                summary=directory/'summary.json'
                if summary.exists() and summary.stat().st_mtime>=job['started']:
                    job['result']=public_report(read(summary))
        active=read(self.runtime/'active-batch.json',{})
        session=read(self.runtime/'session.json',{})
        from .telemetry import recent
        return {'job':job,'hotkey':self.hotkey.status() if getattr(self,'hotkey',None) else None,'can_resume':active.get('status') in ('stopped','needs-attention'),
                'controller':session.get('state','stopped'),'busy':bool(self.process and self.process.poll() is None),
                'timings':recent(self.runtime/'timings.jsonl')}

    def submit(self,payload):
        with self.lock:
            if self.process and self.process.poll() is None:raise ValueError('已有任务运行中，请先等待完成或停止')
            kind=payload.get('kind')
            if kind not in ('scan','preview','start','resume','equip','elixir','dust'):raise ValueError('未知操作')
            request={'kind':kind}
            if kind in ('preview','start','elixir','dust'):
                config=self.config_path(payload.get('config_id'));data=self.store.config(payload['config_id'])
                request['config']=str(config)
                if kind in ('preview','elixir','dust') or not payload.get('fresh',True):
                    snapshot=self.snapshot(payload.get('snapshot_id'))
                    if data['equipment']=='protected' and snapshot['ownership_stale']:
                        raise ValueError('快照之后发生过换装，装备归属可能过期。不借用模式请先重新扫描，或勾选开始时重新扫描。')
                    request['snapshot']=snapshot
                if kind in ('elixir','dust'):
                    identifier=payload.get('demand_id')
                    if identifier not in {d['id'] for d in data['demands']}:raise ValueError('请选择当前配置中的培养目标')
                    request['demand_id']=identifier
            if kind=='equip':
                snapshot=self.snapshot(payload.get('snapshot_id'));request['snapshot']=snapshot
                library=read(self.runtime/'loadouts/library.json',{'loadouts':{}})
                ids=payload.get('ids')
                if not isinstance(ids,list) or not ids or len(ids)!=len(set(ids)) or any(i not in library['loadouts'] for i in ids):raise ValueError('请选择已保存的配装')
                request['ids']=ids
            if kind=='resume' and not self.state()['can_resume']:raise ValueError('没有可继续的强化任务')
            if kind in ('scan','start','equip'):
                active=read(self.runtime/'active-batch.json',{})
                if active.get('status') not in (None,'finished','finished-with-deferred'):
                    raise ValueError('还有未完成的强化任务，请先继续该任务')
            identifier=uuid.uuid4().hex;directory=self.directory/'jobs'/identifier;directory.mkdir(parents=True)
            if 'config' in request:
                compile_config(data,directory/'config')
                request['config']=str(directory/'config')
            request['output']=str(directory/'result.json');save(directory/'request.json',request)
            env=dict(__import__('os').environ,PYTHONIOENCODING='utf-8',PYTHONUNBUFFERED='1')
            process=subprocess.Popen([sys.executable,'-u','-m','enhancer.ui_worker',str(directory/'request.json')],cwd=self.root,
                                     stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',env=env,
                                     creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            job={'id':identifier,'kind':kind,'status':'running','started':time.time(),'logs':deque(maxlen=200),
                 'output':request['output'],'request':request,'stop_requested':False}
            self.process=process;self.job=job;self.jobs[identifier]=job
            threading.Thread(target=self.monitor,args=(process,job,directory),daemon=True).start()
            return {'id':identifier}

    def monitor(self,process,job,directory):
        with (directory/'stdout.log').open('w',encoding='utf-8') as log:
            for line in process.stdout:
                log.write(line);log.flush();job['logs'].append(line.rstrip())
        code=process.wait();job['exit_code']=code;job['finished']=time.time()
        output=read(job['output'],{})
        job['status']='stopped' if job['stop_requested'] else 'completed' if code==0 else 'failed'
        if code==0 and output.get('status')=='finished-with-deferred':job['status']='deferred'
        save(directory/'job.json',{k:v for k,v in job.items() if k!='logs'})
        with self.lock:
            if not (self.process and self.process.poll() is None):
                try:
                    from .storage_management import finish_cleanup
                    cleanup=finish_cleanup(self.runtime,job)
                    save(directory/'screenshot-cleanup.json',cleanup)
                except Exception as exc:
                    save(directory/'screenshot-cleanup.json',{'error':str(exc)})
        self._catalog_cache=None

    def stop(self,expected=None):
        with self.lock:
            if not self.process or self.process.poll() is not None:return {'stopped':False}
            if expected and (expected.get('job_id')!=self.job['id'] or expected.get('kind')!=self.job['kind']):
                raise ValueError('任务已变化，请刷新状态后再停止')
            self.job['stop_requested']=True
            if self.job['kind'] in ('preview','elixir','dust'):self.process.terminate()
            else:(self.runtime/'stop.signal').touch()
            return {'stopped':True}

    def emergency_stop(self):
        # Write first: input/scanner loops can observe this even if another lock is held.
        (self.runtime/'stop.signal').touch()
        result=self.stop()
        save(self.runtime/'emergency-stop.json',{'at':time.time(),'source':'global_hotkey_or_button',
                                                'active_job_stopped':result['stopped']})
        return {'stopped':True,'controller_stop_requested':True}

    def save_loadouts(self):
        if not self.job or self.job['status'] not in ('completed','deferred'):raise ValueError('请先完成一次配装计算')
        request=self.job['request'];result=read(self.job['output'],{})
        allocation=Path(self.job['output'])
        if 'demands' not in result:
            allocation=Path(result.get('directory',''))/'allocation-final.json'
        if not allocation.exists() or 'demands' not in read(allocation,{}):raise ValueError('该任务没有可保存的多需求配装；可先使用只计算')
        from .loadouts import save_allocation
        added=save_allocation(allocation,self.runtime/'loadouts/library.json');self._catalog_cache=None
        return {'saved':added}


def make_handler(backend):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,data,mime='application/json; charset=utf-8'):
            body=json.dumps(data,ensure_ascii=False).encode() if mime.startswith('application/json') else data
            self.send_response(status);self.send_header('Content-Type',mime);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers();self.wfile.write(body)
        def trusted(self):
            host=self.headers.get('Host','')
            expected=f'127.0.0.1:{self.server.server_port}'
            return host==expected and self.headers.get('Origin',f'http://{expected}')==f'http://{expected}'
        def do_GET(self):
            if not self.trusted():self.send(403,{'error':'只允许本地页面访问'});return
            path=urlparse(self.path).path
            if path=='/api/health':
                import ctypes
                self.send(200,{'app':'artifact-workbench','instance':hashlib.sha256(str(ROOT).encode()).hexdigest()[:16],
                               'frozen':bool(getattr(sys,'frozen',False)),
                               'administrator':bool(ctypes.windll.shell32.IsUserAnAdmin()) if sys.platform=='win32' else False});return
            if path=='/api/bootstrap':self.send(200,{'token':backend.token,'instance':hashlib.sha256(str(backend.root.resolve()).encode()).hexdigest()[:16],'catalog':backend.catalog(),'draft':backend.store.latest_draft()});return
            if path=='/api/draft/latest':self.send(200,backend.store.latest_draft());return
            if path=='/api/catalog':self.send(200,backend.catalog(refresh=True));return
            if path=='/api/state':self.send(200,backend.state());return
            if path=='/api/storage':self.send(200,backend.storage());return
            if path=='/api/elixir/latest':self.send(200,backend.latest_elixir());return
            if path=='/api/dust/latest':self.send(200,backend.latest_elixir('dust'));return
            if path.startswith('/api/config/'):
                try:
                    parts=path.strip('/').split('/');identifier=parts[2];backend.config_path(identifier)
                    self.send(200,backend.store.history(identifier) if len(parts)==4 and parts[3]=='history' else backend.store.config(identifier))
                except ValueError as exc:self.send(400,{'error':str(exc)})
                return
            name={'/':'index.html','/app.js':'app.js','/inventory.js':'inventory.js','/storage.js':'storage.js','/dust.js':'dust.js','/longterm.js':'longterm.js','/style.css':'style.css'}.get(path)
            if not name:self.send(404,{'error':'Not found'});return
            mime={'html':'text/html; charset=utf-8','js':'text/javascript; charset=utf-8','css':'text/css; charset=utf-8'}[name.rsplit('.',1)[1]]
            self.send(200,(ASSETS/name).read_bytes(),mime)
        def do_POST(self):
            if not self.trusted() or self.headers.get('X-Local-Token')!=backend.token:
                self.send(403,{'error':'本地会话已失效，请刷新页面'});return
            try:
                path=urlparse(self.path).path
                length=int(self.headers.get('Content-Length','0'))
                limit=12*1024*1024 if path=='/api/inventory/import' else 512000
                if not 0<length<=limit:raise ValueError('请求大小无效')
                data=json.loads(self.rfile.read(length))
                path=urlparse(self.path).path
                if path=='/api/config':result=backend.save_config(data)
                elif path=='/api/inventory/import':result=backend.import_inventory(data)
                elif path=='/api/presets':result=backend.manage_presets(data)
                elif path=='/api/storage':result=backend.manage_storage(data)
                elif path=='/api/draft':result=backend.store.save_draft(data['client'],data['sequence'],data['payload'])
                elif path=='/api/jobs':result=backend.submit(data)
                elif path=='/api/stop':result=backend.stop(data)
                elif path=='/api/emergency-stop':result=backend.emergency_stop()
                elif path=='/api/save-loadouts':result=backend.save_loadouts()
                else:self.send(404,{'error':'Not found'});return
                self.send(200,result)
            except (ValueError,KeyError,TypeError) as exc:self.send(400,{'error':str(exc)})
            except Exception as exc:self.send(500,{'error':str(exc)})
    return Handler


def serve(port=8766):
    backend=Backend();server=ThreadingHTTPServer(('127.0.0.1',port),make_handler(backend))
    from .hotkey import EmergencyHotkey
    backend.hotkey=EmergencyHotkey(backend.emergency_stop,lambda:bool(backend.process and backend.process.poll() is None));backend.hotkey.start()
    url=f'http://127.0.0.1:{server.server_port}'
    save(backend.directory/'server.json',{'url':url,'pid':__import__('os').getpid()})
    print(f'圣遗物工坊：{url}',flush=True)
    try:server.serve_forever()
    finally:backend.hotkey.close();server.server_close()
