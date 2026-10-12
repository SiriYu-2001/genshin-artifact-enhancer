"""Owned GOODScanner backend: scanning, equipment and bounded enhancement."""
from datetime import datetime
import ctypes
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import uuid
from urllib.parse import urlparse

import requests

from .navigation import ROOT
from .job_control import stop_requested
from .batch import save

REVISION='bffc4aad040eac0bb5f10c8b0b2ef86121fb29b5'


class InputNotSent(RuntimeError):
    """The request was positively rejected before any game operation was queued."""


def explain_failure(details):
    if 'Stage observation unreadable' in details:
        fields=(('Material count OCR','材料栏数量（已放入项数/15）'),
                ('material count fraction','材料栏数量（已放入项数/15）'),
                ('Material count label','材料栏标题'),
                ('Enhancement title mismatch','圣遗物名称'),
                ('Current level OCR','当前等级'),
                ('Main stat label mismatch','主属性标签'),
                ('substat layout','副属性区域'),
                ('Substat keys changed','副属性种类'),
                ('Not enhancement page','强化页面标识'))
        field=next((label for token,label in fields if token in details),None)
        if field:
            return f'阶段放入后无法可靠识别{field}，未点击强化。失败诊断目录中的 frame.png、error.json 和 stage-observations.json 可用于排查。'
    for needle,message in (
        ('Artifact filter controls not verified','圣遗物筛选面板未识别完整，已停止后续点击。请确认游戏在“背包 → 圣遗物”页面；失败目录中的 frame.png 和 observations.json 可用于排查。'),
        ('Stage add produced no materials','阶段放入后材料栏仍为空，未点击强化。请检查是否有可用的四星及以下素材或经验材料；若材料充足，请查看保存的阶段诊断。'),
        ('Stage target identity changed','阶段放入前后的目标属性不一致，未点击强化；已保存两次读数供核验。'),
        ('Stage experience changed','阶段放入前后的经验读数发生变化，未点击强化；已保存诊断。'),
        ('Stage material filter changed','阶段放入后的素材筛选不再是四星及以下，已阻止强化。'),
        ('Stage observation unreadable','阶段放入后的画面在限定时间内无法完整识别，未点击强化；失败画面已保存。'),
        ('Unresolved enhancement confirmation','本次强化的结果还未确认。请保持该圣遗物的强化页，点击“核对上次结果”；程序不会重发确认。'),
        ('Game window activation failed','无法把原神切到前台。请点击游戏窗口，再继续任务。'),
        ('Target search interrupted','定位过程中翻页或截图被中断，尚未查完目标列表；这不表示圣遗物不存在。具体终止原因已记录，未确认强化。'),
        ('Exact target not located','未找到属性一致的目标。请检查库存是否已变化，必要时重新导入或扫描。'),
        ('Five-star quick-add','未确认五星快捷放入已关闭。请在游戏的放入设置中关闭五星素材后重试。'),
        ('Five-star or unrecognized material','素材星级未通过检查，本次确认已被阻止。'),
        ('Four known substats','导入数据缺少第4条属性。请扫描当前筛选或更新导出文件。'),
        ('Enhancement title mismatch','无法精确识别当前圣遗物名称，已停止，未据此确认强化；原始失败画面和 OCR 读数已保存。'),
        ('Current level OCR','当前等级无法可靠识别，已停止，未据此确认强化；原始失败画面和等级读数已保存。'),
        ('material count fraction','材料栏数量无法可靠识别，已阻止强化；失败画面已保存。'),
        ('Main stat label mismatch','当前主属性标签未通过识别检查，已停止；失败画面已保存。'),
        ('Enhancement observation failed','当前强化页无法完整识别，已停止；失败画面和具体字段错误已保存。'),
    ):
        if needle in details:return message
    return 'GOODScanner 未完成：'+details


def read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except (ValueError,OSError):return default


def owned_process_running(state):
    if os.name!='nt' or not state.get('pid') or not state.get('exe_path'):return False
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_bool,ctypes.c_ulong];kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    kernel.QueryFullProcessImageNameW.argtypes=[ctypes.c_void_p,ctypes.c_ulong,ctypes.c_wchar_p,ctypes.POINTER(ctypes.c_ulong)]
    kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong];kernel.WaitForSingleObject.restype=ctypes.c_ulong
    # QUERY_LIMITED_INFORMATION | SYNCHRONIZE. An exited process may remain
    # queryable while someone holds a handle; its image path is not liveness.
    handle=kernel.OpenProcess(0x1000|0x100000,False,state['pid'])
    if not handle:
        error=ctypes.get_last_error()
        if error==87:return False  # PID no longer exists (ERROR_INVALID_PARAMETER).
        raise RuntimeError(f'无法核验后台进程是否退出（Windows 错误 {error}），停止标记已保留；请关闭旧工坊并以管理员权限重开')
    try:
        outcome=kernel.WaitForSingleObject(handle,0)
        if outcome==0:return False  # WAIT_OBJECT_0: process has terminated.
        if outcome!=0x102:raise RuntimeError(f'查询后台退出信号失败（Windows 错误 {ctypes.get_last_error()}），停止标记已保留')
        buffer=ctypes.create_unicode_buffer(4096);size=ctypes.c_ulong(len(buffer))
        if not kernel.QueryFullProcessImageNameW(handle,0,buffer,ctypes.byref(size)):
            # Exit can race the image query; recheck the same process object.
            if kernel.WaitForSingleObject(handle,0)==0:return False
            raise RuntimeError(f'无法核验后台程序路径（Windows 错误 {ctypes.get_last_error()}），停止标记已保留')
        return os.path.normcase(buffer.value)==os.path.normcase(state['exe_path'])
    finally:kernel.CloseHandle(handle)


def stop_owned_backend(root=ROOT,grace=2):
    """Stop only the backend whose executable AND instance arguments we own."""
    import psutil
    root=Path(root)
    state=read(root/'runtime/goodscanner/server.json',{})
    if state.get('directory'):
        directory=Path(state['directory']).resolve()
        if not directory.is_relative_to((root/'runtime/goodscanner/instances').resolve()):raise RuntimeError('后台目录不属于当前工坊')
        if directory.exists():(directory/'shutdown.signal').touch()
    deadline=time.monotonic()+grace
    while owned_process_running(state) and time.monotonic()<deadline:time.sleep(.1)
    if owned_process_running(state):
        if not state.get('directory'):raise RuntimeError('后台缺少实例目录，无法安全确认退出；请关闭旧程序')
        try:
            process=psutil.Process(state['pid'])
            expected=(root/'bin/goodscanner/workbench_goodscanner.exe').resolve()
            args=process.cmdline()
            if Path(process.exe()).resolve()!=expected or str(directory/'config.json') not in args or str(directory/'shutdown.signal') not in args:
                raise RuntimeError('后台身份无法核验，保留中断标记；请关闭旧工坊后重试')
            process.kill();process.wait(timeout=2)
        except psutil.NoSuchProcess:pass
        except (psutil.AccessDenied,psutil.TimeoutExpired) as exc:
            raise RuntimeError('后台未能退出，请以管理员权限重开本程序；停止标记已保留') from exc
    if owned_process_running(state):raise RuntimeError('后台尚未退出，停止标记已保留')
    if state:state['state']='stopped';save(root/'runtime/goodscanner/server.json',state)


def legacy_controller_running(root):
    """A stale JSON 'running' flag is not a live legacy controller."""
    import psutil
    state=read(Path(root)/'runtime/session.json',{})
    if state.get('state')!='running':return False
    for key in ('controller_pid','pid'):
        if not state.get(key):continue
        try:
            process=psutil.Process(state[key])
            if key=='pid' and Path(process.exe()).resolve()==(Path(root)/'bin/cocogoat-control.exe').resolve():return True
            if key=='controller_pid' and any(str(Path(root)/'tools'/name).casefold() in ' '.join(process.cmdline()).casefold()
                    for name in ('Start-Controller.ps1','Start-ControllerBootstrap.ps1')):return True
        except psutil.NoSuchProcess:continue
        except psutil.AccessDenied:return True
    return False


def prepare_game_job(root=ROOT):
    """A new explicit game request may clear stop only after the old backend exits."""
    root=Path(root);stop=root/'runtime/stop.signal'
    unique=os.environ.get('ENHANCER_JOB_STOP_FILE')
    if unique and Path(unique).exists():raise RuntimeError('本次任务已中断；请重新开始任务')
    if not stop.exists():return
    stop_owned_backend(root)
    deadline=time.monotonic()+2
    legacy=root/'runtime/session.json'
    while legacy_controller_running(root) and time.monotonic()<deadline:time.sleep(.1)
    if legacy_controller_running(root):raise RuntimeError('旧版霜华控制器仍在运行，请退出旧工坊后重试；当前版不需要手动启动插件')
    if unique and Path(unique).exists():raise RuntimeError('本次任务已中断；请重新开始任务')
    stop.unlink(missing_ok=True)


class GoodClient:
    def __init__(self,state,root=ROOT):
        self.root=Path(root);self.state=state
        url=urlparse(state.get('endpoint',''))
        if url.scheme!='http' or url.hostname!='127.0.0.1' or not url.port or url.username or url.password or url.path:
            raise ValueError('GOODScanner endpoint must be a dedicated loopback port')
        self.http=requests.Session();self.http.trust_env=False
        self.http.headers.update(Authorization='Bearer '+state['token'],Origin='http://127.0.0.1')

    def request(self,method,path,body=None):
        allowed=('/workbench','/health','/status','/result?jobId=','/artifacts?jobId=') if method=='GET' else ('/scan','/equip','/enhance')
        if not any(path==p or (p.endswith('=') and path.startswith(p)) for p in allowed):raise ValueError('Unsupported GOODScanner operation')
        if method=='POST' and stop_requested(self.root):raise InputNotSent('已中断，不再发送游戏操作')
        response=self.http.request(method,self.state['endpoint']+path,json=body,timeout=2 if path in ('/workbench','/health') else 8)
        if not response.ok:
            try:message=response.json().get('error',f'HTTP {response.status_code}')
            except ValueError:message=f'HTTP {response.status_code}'
            error=InputNotSent if method=='POST' and response.status_code in (400,401,403,409,413) else RuntimeError
            raise error('GOODScanner：'+message)
        return response.json()

    def ready(self):
        try:
            value=self.request('GET','/workbench')
            return value.get('instance')==self.state['instance'] and value.get('revision')==REVISION
        except (requests.RequestException,ValueError,RuntimeError):return False

    def run(self,path,payload,directory,stage,timeout=1200):
        window=self.request('GET','/workbench').get('window',{})
        if not window.get('found'):raise RuntimeError('未找到唯一的原神窗口。请先打开游戏；导入 JSON 和计算配装不需要打开游戏。')
        if (window.get('width'),window.get('height'))!=(1920,1080):
            raise RuntimeError(f"自动操作需要 1920×1080，当前游戏窗口为 {window.get('width')}×{window.get('height')}。请调整游戏分辨率后重试。")
        if stage in ('open','scan','preflight'):print(json.dumps({'phase':'preflight','message':'已确认 1920×1080 游戏窗口，接下来自动切到游戏'},ensure_ascii=False),flush=True)
        directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
        intent={'stage':stage,'payload':payload,'state':'submitting','started':time.time()}
        save(directory/'request.json',intent)
        # POST exactly once. A timeout is not permission to replay an equip action.
        answer=self.request('POST',path,payload)
        identifier=answer.get('jobId')
        try:uuid.UUID(identifier)
        except (ValueError,TypeError,AttributeError):raise RuntimeError('GOODScanner 返回无效任务编号；没有重复提交')
        intent.update(state='submitted',job_id=identifier);save(directory/'request.json',intent)
        deadline=time.monotonic()+timeout;notice=0;last=None;advanced=time.monotonic();completed=None
        while time.monotonic()<deadline:
            if stop_requested(self.root):raise RuntimeError('已请求中断；未确认操作保留，不自动重发')
            blocked=read(Path(self.state['directory'])/'failures'/f'input-{identifier}.json',{})
            if blocked.get('jobId')==identifier and blocked.get('reason')=='game_focus_lost':
                raise RuntimeError('原神已失去前台焦点，已停止鼠标键盘输入。请切回游戏：扫描或强化从“背包 → 圣遗物”开始，换装可从“角色 → 圣遗物”开始。未确认的消耗不会重复执行。')
            state=self.request('GET','/status')
            if state.get('jobId')!=identifier:raise RuntimeError('GOODScanner 任务编号发生变化，停止读取旧结果')
            if state.get('state')=='completed':break
            if state.get('state') not in ('running','pending','queued'):raise RuntimeError('GOODScanner 未正常执行任务：'+str(state.get('state')))
            progress=state.get('scanProgress',{}).get('artifacts',{}) if path=='/scan' else state.get('progress',{})
            pair=(progress.get('completed',0),progress.get('total',0))
            if path=='/scan':
                if pair[0]!=completed:completed=pair[0];advanced=time.monotonic()
                elif time.monotonic()-advanced>=90:
                    # Do not pretend a stuck final page is a complete inventory.
                    save(directory/'stalled.json',{'job_id':identifier,'completed':completed,'total':pair[1],
                        'reason':'no_scan_progress_90_seconds'})
                    (self.root/'runtime/stop.signal').touch()
                    stop_owned_backend(self.root)
                    raise RuntimeError('扫描连续 90 秒没有新增识别结果，已结束后台，不再点击。请确认停在背包圣遗物页；可重新扫描当前筛选或导入 JSON。请提供本次 stdout.log 与失败目录，不能将这次结果视为完整库存。')
            if pair!=last and time.monotonic()-notice>=2:
                print(json.dumps({'phase':'scanning' if path=='/scan' else 'good_enhancement' if path=='/enhance' else 'good_equipment','backend':'GOODScanner','stage':stage,
                                  'completed':pair[0],'total':pair[1]},ensure_ascii=False),flush=True)
                last=pair;notice=time.monotonic()
            time.sleep(.25)
        else:
            (self.root/'runtime/stop.signal').touch();stop_owned_backend(self.root)
            raise RuntimeError('GOODScanner 任务超时，已结束后台；未确认操作不会自动重发')
        result=self.request('GET','/result?jobId='+identifier)
        save(directory/'result.json',result)
        expected=['artifacts'] if path=='/scan' else ['enhance:0'] if path=='/enhance' else [f'equip:{i}' for i in range(len(payload['equip']))]
        rows=result.get('results',[])
        if len(rows)!=len(expected) or {r.get('id') for r in rows}!=set(expected):raise RuntimeError('GOODScanner 结果缺项或重复，不能视为完成')
        good={'already_correct'} if payload.get('verifyOnly') else {'success','already_correct'}
        failed=[r for r in rows if r.get('status') not in good]
        if failed:raise RuntimeError(explain_failure('；'.join(str(r.get('message',r.get('status'))) for r in failed[:3])))
        return identifier,result


def ensure_backend(root=ROOT):
    root=Path(root);runtime=root/'runtime';runtime.mkdir(exist_ok=True)
    directory=runtime/'goodscanner';directory.mkdir(exist_ok=True)
    executable=root/'bin/goodscanner/workbench_goodscanner.exe'
    if not executable.is_file():raise RuntimeError('发行包缺少 GOODScanner 后台程序，请完整解压新版 ZIP')
    digest=hashlib.sha256(executable.read_bytes()).hexdigest()
    state=read(directory/'server.json',{})
    if state.get('state')=='running' and state.get('exe_hash')==digest:
        client=GoodClient(state,root)
        if client.ready():return client
    if os.name!='nt' or not ctypes.windll.shell32.IsUserAnAdmin():raise RuntimeError('请以管理员权限启动工坊，GOODScanner 不会另行重复申请权限')
    if stop_requested(root):raise RuntimeError('存在中断标记，请从界面开始新任务后重试')
    instance=uuid.uuid4().hex;work=directory/'instances'/instance;(work/'data').mkdir(parents=True)
    mapping=root/'data/goodscanner-mappings.json'
    if not mapping.is_file():raise RuntimeError('发行包缺少 GOODScanner 离线游戏映射')
    (work/'data/mappings.json').write_bytes(mapping.read_bytes())
    config={'lang':'zh','scan_characters':False,'scan_weapons':False,'scan_artifacts':True,'scan_achievements':False,
            'dump_images':False,'dump_job_data':False,'save_on_cancel':False,'only_keep_latest_export':False,
            'ocr_pool_v4_override':2,'ocr_pool_v5_override':2}
    config.update(read(directory/'settings.json',{}))
    config.update(dump_images=False,dump_job_data=False,save_on_cancel=False)
    save(work/'config.json',config)
    with socket.socket() as probe:probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    token=secrets.token_urlsafe(32)
    env=dict(os.environ,WORKBENCH_GOOD_TOKEN=token,WORKBENCH_GOOD_INSTANCE=instance,
             WORKBENCH_ENHANCE_DIR=str(directory/'enhancement'),
             WORKBENCH_STOP_FILE=str(runtime/'stop.signal'),ORT_DYLIB_PATH=str(root/'bin/onnxruntime.dll'),RAYON_NUM_THREADS='4')
    shutdown=work/'shutdown.signal'
    with (work/'server.log').open('wb') as log:
        process=subprocess.Popen([str(executable),str(port),str(work/'config.json'),str(shutdown)],cwd=work,env=env,
                                 stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    state={'state':'starting','pid':process.pid,'instance':instance,'endpoint':f'http://127.0.0.1:{port}',
           'token':token,'directory':str(work),'exe_hash':digest,'exe_path':str(executable),'revision':REVISION}
    save(directory/'server.json',state);client=GoodClient(state,root)
    deadline=time.monotonic()+40
    while time.monotonic()<deadline:
        if client.ready():
            state['state']='running';save(directory/'server.json',state)
            print(json.dumps({'phase':'good_ready','backend':'GOODScanner','message':'包内 GOODScanner 已就绪；扫描、穿戴与强化使用统一后台'},ensure_ascii=False),flush=True)
            return client
        if process.poll() is not None:break
        time.sleep(.25)
    shutdown.touch();state['state']='failed';save(directory/'server.json',state)
    raise RuntimeError('GOODScanner 启动失败，请查看 runtime/goodscanner/instances 下的 server.log')


def scan_inventory(scope='all'):
    if scope not in ('all','current'):raise ValueError('扫描范围必须为当前筛选或全部库存')
    from .import_inventory import parse_document
    client=ensure_backend();directory=ROOT/'runtime/goodscanner/scans'/('scan-'+uuid.uuid4().hex)
    directory.mkdir(parents=True,exist_ok=True)
    identifier,result=client.run('/scan',{'characters':False,'weapons':False,'artifacts':True,'achievements':False,'artifactMode':'all','preserveFilters':scope=='current'},
                                 directory,'scan',timeout=2400)
    artifacts=client.request('GET','/artifacts?jobId='+identifier)
    if not isinstance(artifacts,list) or not artifacts:raise RuntimeError('GOODScanner 没有返回本次任务的完整圣遗物数据')
    audit=read(Path(client.state['directory'])/'receipts'/f'scan-{identifier}.json',{})
    validate_scan_audit(audit,identifier,len(artifacts),scope)
    save(directory/'coverage.json',audit)
    document={'format':'GOOD','version':3,'source':'GOODScanner','artifacts':artifacts}
    save(directory/'good.json',document)
    rows,summary=parse_document(json.dumps(document))
    save(directory/'validation.json',summary)
    if not summary['can_import']:raise RuntimeError(f"GOODScanner 数据校验未通过：{summary['error_count']} 件；详见本轮 validation.json")
    if summary['unknown_kind']:raise RuntimeError('GOODScanner 导出缺少定制标记，不能将不完整元数据当成新扫描')
    for row in rows:
        # These are a roll solver's chosen explanation, not observed history.
        # Our Dust evaluator must retain all feasible histories from displayed values.
        row['computed_roll_hints']=row.get('import_metadata',{})
        row['import_metadata']={}
    save(directory/'enhancer-artifacts.json',rows)
    save(directory/'scan-count.json',{'requested':len(rows),'source':'goodscanner','game_scan_verified':scope=='all','scope':'five_star','scan_scope':scope,
                                     'inventory_total':audit['expected'],'visited':audit['visited'],'skipped_lower_rarity':audit['skipped_lower_rarity']})
    save(directory/'goodscanner-info.json',{'revision':REVISION,'job_id':identifier,'summary':summary,'scope':'当前筛选已读取库存' if scope=='current' else '全部五星库存'})
    print(json.dumps({'scan_finished':str(directory),'backend':'GOODScanner','five_star':len(rows)},ensure_ascii=False),flush=True)
    return directory


def validate_scan_audit(audit,identifier,count,scope='all'):
    keys=('expected','visited','five_star','accepted','skipped_lower_rarity','unknown_rarity','missed','skipped_positions')
    valid=all(type(audit.get(k)) is int and audit[k]>=0 for k in keys)
    endings=('Exhausted','EmptyCell','UnchangedPage') if scope=='current' else ('Exhausted',)
    scoped=audit.get('scope')=='observed_filter_results' and audit.get('account_complete') is False
    valid=valid and audit.get('jobId')==identifier and audit.get('complete') is True and audit.get('termination') in endings
    valid=valid and (scoped if scope=='current' else audit.get('scope') in (None,'full_inventory'))
    valid=valid and audit['expected']==audit['visited']==audit['five_star']+audit['skipped_lower_rarity']
    valid=valid and audit['five_star']==audit['accepted']==count and not any(audit[k] for k in ('unknown_rarity','missed','skipped_positions'))
    if not valid:raise RuntimeError('GOODScanner 缺少完整遍历凭据或数量不符；本轮库存不会发布为完整扫描')


def set_key_to_good(key):
    from .sets import SET_LABELS
    mapping=read(ROOT/'data/goodscanner-mappings.json',{})
    found=[r['id'] for r in mapping.get('artifactSets',[]) if r.get('n',{}).get('zh')==SET_LABELS.get(key)]
    if len(found)!=1:raise ValueError('GOODScanner 套装映射不明确：'+key)
    return found[0]


def character_key(aliases):
    mapping=read(ROOT/'data/goodscanner-mappings.json',{})
    found={r['id'] for r in mapping.get('characters',[]) if r.get('n',{}).get('zh') in aliases}
    if len(found)!=1:raise ValueError('GOODScanner 无法唯一定位角色，请补充游戏中的正式角色名')
    return next(iter(found))


def good_artifact(artifact):
    if artifact.rarity!=5 or artifact.level!=20 or len(artifact.stats)!=4 or any(s.pending for s in artifact.stats):
        raise ValueError('当前 GOODScanner 穿戴适配仅接受已核验完整属性的 +20 五星物品')
    return {'setKey':set_key_to_good(artifact.set_key),'slotKey':artifact.slot,'mainStatKey':artifact.main,'level':20,'rarity':5,
            'substats':[{'key':s.key,'value':float(s.value)} for s in artifact.stats],'location':'','lock':artifact.locked,
            'elixirCrafted':artifact.special=='defined'}


def wire_identity(row):
    return (row.get('setKey'),row.get('slotKey'),row.get('mainStatKey'),row.get('level'),row.get('rarity'),
            tuple(sorted((s['key'],round(float(s['value']),6)) for s in row.get('substats',[]))),
            tuple(sorted((s['key'],round(float(s['value']),6)) for s in row.get('unactivatedSubstats',[]))))


def validate_receipts(receipts,payload,job_id,owner):
    receipts=[r for r in receipts if r and r.get('jobId')==job_id and r.get('sameFrame') is True and r.get('ownerKey')==owner]
    from collections import Counter
    expected=Counter(wire_identity(r['artifact']) for r in payload['equip'])
    actual=Counter(wire_identity(r.get('expected',{})) for r in receipts)
    if actual!=expected or any(not r.get('matchDetails') or not r.get('ownerRaw') for r in receipts):
        raise RuntimeError('GOODScanner 未返回每一件的同帧属性与归属复核凭据')
    return receipts


def apply_loadouts(library_path,ids,scan,updates=None,policy='strict',*,full_audit=False):
    from .loadouts import load_library,plan_file,fingerprint
    from .report import load_scan
    from .batch import apply_updates
    plan=plan_file(library_path,ids,scan,updates,policy)
    if plan['status']=='blocked':raise RuntimeError('配装包含缺失、重复或冲突的物品，未启动换装')
    library=load_library(library_path);pool,_,_=load_scan(scan)
    if updates:pool=apply_updates(pool,read(updates,{}))
    lookup={a.id:a for a in pool}
    prior=read(ROOT/'runtime/active-equip.json',{})
    if prior.get('directory') and (Path(prior['directory'])/'equip-pending.json').exists():
        raise RuntimeError('上次换装尚未确认。先核对原任务状态；不会自动重复提交装备请求')
    directory=ROOT/'runtime'/datetime.now().strftime('equip-good-%Y%m%d-%H%M%S-%f');directory.mkdir(parents=True)
    save(directory/'plan.json',plan)
    result={'status':'running','backend':'GOODScanner','directory':str(directory),'inventory_source':str(scan),'loadouts':[]}
    def record():
        save(directory/'result.json',result);save(ROOT/'runtime/active-equip.json',result)
    record()
    try:
        client=ensure_backend()
        if client.request('GET','/workbench').get('inlineEquipVerification') is not True:
            raise RuntimeError('当前后台尚不支持逐件换装回执，请更新配套后台；没有回退到三遍检查，也没有发送换装操作。')
        for row in plan['loadouts']:
            if row['status']!='ready':continue
            import re
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',row['id']):raise ValueError('配装 ID 无效')
            entry=library['loadouts'][row['id']][-1]
            owner=character_key(entry.get('character_aliases',[entry['character']]))
            targets=[lookup[r['artifact_id']] for r in row['items']]
            if len(targets)!=5 or len({a.slot for a in targets})!=5:raise ValueError('需要完整五件配装')
            payload={'equip':[{'artifact':good_artifact(a),'location':owner} for a in targets],
                     'allowBorrow':entry.get('equipment_policy')=='borrow'}
            pending=directory/'equip-pending.json';save(pending,{'loadout':row['id'],'payload':payload,'phase':'submitting'})
            job_id,applied=client.run('/equip',payload,directory/row['id']/'apply','equip')
            save(pending,{'loadout':row['id'],'payload':payload,'phase':'awaiting_inline_receipts','job_id':job_id})
            receipts=[read(p) for p in (Path(client.state['directory'])/'receipts').glob('verify-*.json')]
            receipts=validate_receipts(receipts,payload,job_id,owner)
            save(directory/row['id']/'verification-receipts.json',receipts)
            if full_audit:
                audit_id,_=client.run('/equip',payload|{'verifyOnly':True},directory/row['id']/'audit','audit')
                audit_receipts=[read(p) for p in (Path(client.state['directory'])/'receipts').glob('verify-*.json')]
                save(directory/row['id']/'audit-receipts.json',validate_receipts(audit_receipts,payload,audit_id,owner))
            statuses={r['id']:r['status'] for r in applied['results']}
            refs={r['attributes']['slot']:r for r in entry['items']}
            items=[{'slot':a.slot,'name':refs[a.slot]['name'],'status':'equipped' if statuses[f'equip:{i}']=='success' else 'already_correct',
                    'attributes':refs[a.slot]['attributes']} for i,a in enumerate(targets)]
            verified=[{'slot':a.slot,'fingerprint':fingerprint(a),'owner':entry['character']+'已装备','backend':'GOODScanner'} for a in targets]
            result['loadouts'].append({'id':row['id'],'character':entry['character'],'items':items,'verified':verified})
            pending.unlink();record()
        result['status']='verified'
    except Exception as exc:
        result.update(status='needs-attention',error=str(exc));raise
    finally:record()
    print(json.dumps({'backend':'GOODScanner','status':'verified','characters':len(result['loadouts'])}),flush=True)
    return result
