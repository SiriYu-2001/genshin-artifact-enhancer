"""Sequential policy over GOODScanner's durable single-step executor.

No legacy yas subprocess, Frostflake, screen coordinates, or LLM decisions.
Scoring and legal-roll transitions remain in the existing mathematical modules.
"""
from dataclasses import replace
from datetime import datetime
from fractions import Fraction as F
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

from .navigation import ROOT
from .job_control import stop_requested
from .batch import apply_updates,save
from .model import Profile,Stat,evaluate
from .capped import CappedInventory
from .report import load_scan
from .transitions import enhancement_result
from .good_backend import ensure_backend,read,set_key_to_good,wire_identity,InputNotSent
from .stage_proof import StageProof,validate_restricted_stage


def wire(a):
    if a.rarity!=5 or len(a.stats)!=4:raise ValueError('需要四条已知属性的五星强化目标')
    return {'setKey':set_key_to_good(a.set_key),'slotKey':a.slot,'mainStatKey':a.main,'level':a.level,'rarity':5,
            'substats':[{'key':s.key,'value':float(s.value)} for s in a.stats if not s.pending],
            'unactivatedSubstats':[{'key':s.key,'value':float(s.value)} for s in a.stats if s.pending],
            'location':'','lock':a.locked,'elixirCrafted':a.special=='defined'}


def request_matches(a,b):
    if not isinstance(a,dict) or not isinstance(b,dict):return False
    if any(a.get(k)!=b.get(k) for k in ('operationId','action','name')) or a.get('sourceKind','ordinary')!=b.get('sourceKind','ordinary'):return False
    x,y=a.get('artifact',{}),b.get('artifact',{})
    return (wire_identity(x)==wire_identity(y) and all(x.get(k,default)==y.get(k,default)
        for k,default in (('lock',False),('location',''),('elixirCrafted',False),('astralMark',False))))


def validate_result(previous,profile,payload,result):
    if not request_matches(result.get('request'),payload) or result.get('confirmed') is not True:
        raise RuntimeError('强化回执与原请求不一致')
    before=result['before'];after=result['after'];proof=result['materialProof']
    wanted={s.key:float(s.value) for s in previous.stats}
    if before['level']!=previous.level or before['stats']!=wanted or before['materialCount']!=0:
        raise RuntimeError('强化前目标属性不一致')
    if proof.get('sameFrame') is not True or proof.get('selection')!='restricted_game_stage_add':
        raise RuntimeError('缺少同帧素材批次证据')
    stage=StageProof(proof.get('emptyBefore') is True,proof.get('rarityLimit'),
                     proof.get('fiveStarQuickAddDisabled') is True,proof.get('stageAddObserved') is True,
                     'GOODScanner:'+payload['operationId'])
    validate_restricted_stage(stage,proof['count'],proof['visibleRarities'])
    if after['materialCount']!=0 or not (after['level']>previous.level or
            after['level']==previous.level and isinstance(after.get('exp'),int) and isinstance(before.get('exp'),int) and after['exp']>before['exp']):
        raise RuntimeError('强化结果尚未核实，不允许重复确认')
    if set(after['stats'])!=set(wanted):raise RuntimeError('结果副词条种类改变')
    fields={f'sub_value_{i}':str(after['stats'][s.key]) for i,s in enumerate(previous.stats)}
    actual=enhancement_result(previous,SimpleNamespace(level=after['level']),fields,profile)
    if wire_identity(result['artifact'])!=wire_identity(wire(actual)):
        raise RuntimeError('原生执行器与策略层对结果的解释不同')
    return actual


def before_from_payload(template,payload):
    """Recover pre-operation values even after a partial local receipt commit."""
    original=payload['artifact'];current=wire(template)
    if any(original[k]!=current[k] for k in ('setKey','slotKey','mainStatKey','rarity')):
        raise RuntimeError('待核对操作属于另一件物品')
    stats=[Stat(s['key'],F(str(s['value'])),False) for s in original.get('substats',[])]+[Stat(s['key'],F(str(s['value'])),True) for s in original.get('unactivatedSubstats',[])]
    return replace(template,level=original['level'],stats=tuple(stats),special=payload.get('sourceKind','ordinary'))


def main(reconcile_only=False):
    active=read(ROOT/'runtime/active-run.json',{})
    if not active:raise RuntimeError('没有待执行的强化任务')
    run=Path(active['run_directory']);run.mkdir(parents=True,exist_ok=True)
    (run/'last-error.json').unlink(missing_ok=True)
    target_id=active['target_id'];profile=Profile.load(active['profile'])
    if active.get('ownership')=='no-borrow':profile.data['allowed_equipped_characters']=profile.data['character_aliases']
    pool,names,coverage=load_scan(active['scan_directory'])
    if not coverage['complete']:raise RuntimeError('库存不完整')
    if active.get('inventory_updates_path'):pool=apply_updates(pool,read(active['inventory_updates_path'],{}))
    target=next(a for a in pool if a.id==target_id)
    state=read(run/'target-state.json')
    if state:target=replace(target,level=state['level'],stats=tuple(Stat.from_dict(s) for s in state['substats']),special=state.get('enhancement_kind',target.special))
    # Properties locate a physical item only when that identity is unique.
    identity=wire_identity(wire(target))
    duplicates=[a.id for a in pool if a.id!=target_id and a.rarity==5 and len(a.stats)==4 and wire_identity(wire(a))==identity]
    if duplicates:raise RuntimeError('存在属性完全相同的另一件圣遗物，不能确定目标身份；未操作游戏')
    baseline=CappedInventory(pool,profile);client=ensure_backend()
    if client.request('GET','/workbench').get('enhancementVersion')!=2:
        raise RuntimeError('包内 GOODScanner 尚不支持新版强化协议，请使用完整的新发行包')
    pending_path=run/'pending.json'

    def call(action,payload=None):
        body=payload or {'action':action,'operationId':str(uuid.uuid4()),'artifact':wire(target),'name':names[target_id],'sourceKind':target.special}
        directory=run/'native'/body['operationId']
        try:
            _,response=client.run('/enhance',body,directory,action,timeout=600)
        except Exception as exc:
            save(run/'last-error.json',{'stage':action,'operation_id':body['operationId'],'message':str(exc),'time':datetime.now().isoformat()})
            raise
        result=json.loads(response['results'][0]['message'])
        if action!='reconcile' and not request_matches(result.get('request'),body):raise RuntimeError('原生强化请求身份不一致')
        return body,result

    def complete(pending,result):
        nonlocal target
        before=before_from_payload(target,pending['payload'])
        actual=validate_result(before,profile,pending['payload'],result)
        if target.level>actual.level:raise RuntimeError('本地已有更新的目标状态，不能用旧回执覆盖')
        item={'index':int(target_id.rsplit(':',1)[1]),'name':names[target_id],'setKey':actual.set_key,
              'slotKey':actual.slot,'mainStatKey':actual.main,'rarity':5,'level':actual.level,'lock':actual.locked,
              'equipped':actual.equipped,'equip_raw':'','special':actual.special!='ordinary','enhancement_kind':actual.special,'source':'GOODScanner_enhancement',
              'substats':[{'key':s.key,'value':float(s.value),'pending':s.pending} for s in actual.stats]}
        def old_observation(o):return {'level':o['level'],'exp':o['exp'],'next_level_exp':o['nextExp'],
            'material_slots_used':o['materialCount'],'title':o['title'],'mora':None,'mora_cost':None,'material_capacity':15}
        proof=result['materialProof']
        receipt={'id':pending['id'],'target_id':target_id,'before':old_observation(result['before']),
                 'after':old_observation(result['after']),'actual_artifact':item,'method':'GOODScanner_stage_add',
                 'material':'restricted-stage selection','slots':proof['count'],'visible_rarities':proof['visibleRarities'],
                 'selection_proof':{'empty_before':True,'rarity_limit':4,'five_star_quick_add_disabled':True,'stage_add_observed':True},
                 'native_receipt':result,'finished':datetime.now().isoformat(),'progress_action':'reevaluate'}
        save(run/f"receipt-{pending['id']}.json",receipt);save(run/'target-state.json',item)
        pending_path.unlink();target=actual

    if pending_path.exists():
        pending=read(pending_path)
        uuid.UUID(pending['id'])
        if pending.get('backend')!='GOODScanner' or pending['target_id']!=target_id:
            raise RuntimeError('旧执行器存在未确认操作，必须先核对原记录，不能迁移后重复消耗')
        # A persisted native receipt can reconcile after either process restarts.
        native=read(ROOT/'runtime/goodscanner/enhancement'/f"{pending['id']}.json")
        if native and native.get('confirmed') is True:complete(pending,native)
        elif native and native.get('confirmed') is False and request_matches(native.get('request'),pending['payload']):
            pending_path.unlink()  # Authoritative evidence: no confirmation issued.
        else:
            _,result=call('reconcile');complete(pending,result)
    if reconcile_only:
        from .job_control import pending_operations
        if pending_operations(ROOT/'runtime'):raise RuntimeError('仍有未核对的操作记录；没有继续强化')
        return {'status':'reconciled','level':target.level,'game_consumption':False}
    _,opened=call('open')
    if opened.get('sourceVerified') is not True or wire_identity(opened.get('artifact',{}))!=wire_identity(wire(target)):
        raise RuntimeError('未取得完整的目标属性与定制状态核验结果')
    kind='defined' if opened['artifact'].get('elixirCrafted') is True else 'ordinary'
    target=replace(target,special=kind)
    save(run/'target-state.json',{'index':int(target_id.rsplit(':',1)[1]),'name':names[target_id],
        'setKey':target.set_key,'slotKey':target.slot,'mainStatKey':target.main,'rarity':5,'level':target.level,'lock':target.locked,
        'equipped':target.equipped,'equip_raw':'','special':kind=='defined','enhancement_kind':kind,'source':'GOODScanner_source_verification',
        'substats':[{'key':s.key,'value':float(s.value),'pending':s.pending} for s in target.stats]})
    print(json.dumps({'phase':'source_verified','kind':kind,'message':'已核实为普通圣遗物' if kind=='ordinary' else '已核实为定制圣遗物，保底信息未完整时暂不自动培养'},ensure_ascii=False),flush=True)
    while True:
        if stop_requested(ROOT):raise RuntimeError('已中断')
        decision=({'id':target.id,'level':target.level,'action':'complete' if target.level==20 else 'enhance',
                   'reason':'bootstrap_mature_build','bootstrap':True,'probability_lower':None,
                   'next_checkpoint':min(20,(target.level//4+1)*4)} if baseline.baseline is None else evaluate(target,pool,profile,baseline))
        if target.special!='ordinary' and target.level<20:decision={'id':target.id,'level':target.level,'action':'unsupported','reason':'defined_guarantee_state_required'}
        with (run/'decisions.jsonl').open('a',encoding='utf-8') as log:log.write(json.dumps(decision,ensure_ascii=False)+'\n')
        if decision['action']!='enhance':
            save(run/'decision-final.json',decision)
            call('leave')
            print(json.dumps({'stopped':True,'decision':decision,'backend':'GOODScanner'},ensure_ascii=False),flush=True)
            return
        payload={'action':'step','operationId':str(uuid.uuid4()),'artifact':wire(target),'name':names[target_id],'sourceKind':target.special}
        pending={'backend':'GOODScanner','id':payload['operationId'],'target_id':target_id,'payload':payload}
        save(pending_path,pending)
        try:
            _,result=call('step',payload);complete(pending,result)
        except Exception as exc:
            native=read(ROOT/'runtime/goodscanner/enhancement'/f"{pending['id']}.json")
            not_submitted=not (run/'native'/pending['id']/'request.json').exists()
            if not_submitted or isinstance(exc,InputNotSent) or (native and native.get('confirmed') is False and request_matches(native.get('request'),payload)):
                pending_path.unlink(missing_ok=True)
            raise
        print(json.dumps({'level':target.level,'name':names[target_id],'character':profile.data['character'],'probability_before':decision['probability_lower'],'backend':'GOODScanner'},ensure_ascii=False),flush=True)
