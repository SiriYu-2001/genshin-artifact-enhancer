"""Explicit, non-consuming equipment recovery using the original exact targets."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from .batch import save


def recovery_state(runtime):
    from .good_backend import read
    runtime=Path(runtime)
    result=read(runtime/'active-equip.json',{})
    if result.get('backend')!='GOODScanner' or result.get('status') not in ('needs-attention','cancelled','running'):
        return None
    directory=Path(result.get('directory','')).resolve()
    if not directory.is_relative_to(runtime.resolve()) or not (directory/'plan.json').is_file():return None
    return {'available':True,'status':result['status'],'error':result.get('error',''),
            'characters':[r.get('character','') for r in read(directory/'plan.json',{}).get('loadouts',[])],
            'completed_characters':len(result.get('loadouts',[])),
            'hint':'仅重试穿戴，先核对每件完整属性和当前装备者；已经穿对的跳过，不会重跑强化。'}


def queue_from_library(plan,library):
    """Recover older installs using recorded revisions, never the latest revision."""
    from .good_backend import character_key,set_key_to_good
    from .loadouts import fingerprint
    queue=[]
    for row in plan['loadouts']:
        if row['status']!='ready':continue
        records=[e for e in library['loadouts'].get(row['id'],[]) if e['revision']==row['revision']]
        if len(records)!=1:raise ValueError('原配装版本已丢失，请重新计算并保存配装；不会改用其他版本')
        entry=records[0];by_slot={r['attributes']['slot']:r for r in entry['items']}
        expected={r['slot']:r['fingerprint'] for r in row['items']}
        if len(expected)!=5 or set(expected)!=set(by_slot):raise ValueError('原换装计划不完整')
        owner=character_key(entry.get('character_aliases',[entry['character']]))
        targets=[]
        for slot,digest in expected.items():
            ref=by_slot[slot];a=ref['attributes']
            if digest!=ref['fingerprint'] or digest!=fingerprint(a) or a['level']!=20 or a['rarity']!=5:
                raise ValueError('原换装计划与保存属性不一致')
            targets.append({'location':owner,'artifact':{'setKey':set_key_to_good(a['set_key']),
                'slotKey':slot,'mainStatKey':a['main'],'level':20,'rarity':5,'lock':False,'location':'',
                'substats':[{'key':s['key'],'value':s['value']} for s in a['substats']]}})
        queue.append({'id':row['id'],'character':entry['character'],'entry':entry,
                      'payload':{'equip':targets,'allowBorrow':entry.get('equipment_policy')=='borrow'}})
    if not queue:raise ValueError('没有可恢复的完整配装')
    return queue


def execute_queue(directory,result,queue,client,*,full_audit=False,allow_borrow=False):
    from . import good_backend as good
    from .loadouts import fingerprint
    directory=Path(directory)
    attempt=directory/('attempt-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    attempt.mkdir()
    def record():
        save(directory/'result.json',result);save(good.ROOT/'runtime/active-equip.json',result)
    result.update(status='running');result.pop('error',None);record()
    try:
        if client.request('GET','/workbench').get('inlineEquipVerification') is not True:
            raise RuntimeError('当前后台尚不支持逐件换装回执，请更新配套后台；没有回退到三遍检查，也没有发送换装操作。')
        for row in queue:
            entry=row['entry'];payload=deepcopy(row['payload'])
            if allow_borrow:payload['allowBorrow']=True
            pending=directory/'equip-pending.json'
            save(pending,{'loadout':row['id'],'payload':payload,'phase':'submitting','attempt':str(attempt)})
            job_id,applied=client.run('/equip',payload,attempt/row['id']/'apply','equip')
            save(pending,{'loadout':row['id'],'payload':payload,'phase':'awaiting_inline_receipts','job_id':job_id})
            receipts=[good.read(p) for p in (Path(client.state['directory'])/'receipts').glob('verify-*.json')]
            owner=payload['equip'][0]['location']
            receipts=good.validate_receipts(receipts,payload,job_id,owner)
            save(attempt/row['id']/'verification-receipts.json',receipts)
            if full_audit:
                audit_id,_=client.run('/equip',payload|{'verifyOnly':True},attempt/row['id']/'audit','audit')
                receipts=[good.read(p) for p in (Path(client.state['directory'])/'receipts').glob('verify-*.json')]
                save(attempt/row['id']/'audit-receipts.json',good.validate_receipts(receipts,payload,audit_id,owner))
            statuses={r['id']:r['status'] for r in applied['results']}
            refs={r['attributes']['slot']:r for r in entry['items']}
            items=[{'slot':t['artifact']['slotKey'],'name':refs[t['artifact']['slotKey']]['name'],
                    'status':'equipped' if statuses[f'equip:{i}']=='success' else 'already_correct',
                    'attributes':refs[t['artifact']['slotKey']]['attributes']} for i,t in enumerate(payload['equip'])]
            verified=[{'slot':a['slot'],'fingerprint':fingerprint(a['attributes']),
                       'owner':entry['character']+'已装备','backend':'GOODScanner'} for a in items]
            result['loadouts']=[r for r in result['loadouts'] if r['id']!=row['id']]
            result['loadouts'].append({'id':row['id'],'character':entry['character'],'items':items,'verified':verified})
            pending.unlink();record()
        result['status']='verified'
    except Exception as exc:
        result.update(status='needs-attention',error=str(exc));raise
    finally:record()
    return result


def retry_equipment(*,allow_borrow=False):
    from . import good_backend as good
    from .job_control import pending_operations,stop_requested
    runtime=good.ROOT/'runtime'
    if type(allow_borrow) is not bool:raise ValueError('本次借用选项必须为布尔值')
    if not recovery_state(runtime):raise ValueError('没有可重试的换装任务')
    if pending_operations(runtime):raise ValueError('请先核对未确认的强化结果；换装恢复不会确认或重发强化')
    batch=good.read(runtime/'active-batch.json',{})
    if batch.get('status') not in (None,'finished','finished-with-deferred','cancelled'):
        raise ValueError('请先完成或结束当前强化任务，再恢复穿戴')
    result=good.read(runtime/'active-equip.json');directory=Path(result['directory'])
    queue=good.read(directory/'equipment-queue.json')
    if queue is None:
        from .loadouts import load_library
        queue=queue_from_library(good.read(directory/'plan.json'),load_library(runtime/'loadouts/library.json'))
        save(directory/'equipment-queue.json',queue)
    # An explicit retry cannot race a still-running old equipment request.
    good.stop_owned_backend(good.ROOT)
    if stop_requested(good.ROOT):raise RuntimeError('已请求停止，未开始换装重试')
    pending=directory/'equip-pending.json'
    if pending.exists():
        pending.replace(directory/('equip-retry-journal-'+datetime.now().strftime('%Y%m%d-%H%M%S-%f')+'.json'))
    save(directory/'retry-policy.json',{'allow_borrow_this_attempt':allow_borrow})
    return execute_queue(directory,result,queue,good.ensure_backend(),allow_borrow=allow_borrow)
