"""Offline inventory-wide Dust recommendations with explicit global pity."""
from copy import deepcopy
from collections import Counter
from pathlib import Path
from fractions import Fraction as F
import json
import math
import re

from .dust import DustAdvisor,COST,transition
from .model import Profile,ROLLS
from .loadouts import fingerprint
from .sets import SET_LABELS


def settings(value=None):
    default={'budget':0,'points':0,'phase':0,'objective':'efficiency','respect_priority':True,'metadata':{},'longterm':None}
    if value is None:value={}
    if not isinstance(value,dict) or set(value)-set(default):raise ValueError('启圣之尘设置格式无效')
    d=default|deepcopy(value)
    from .longterm import settings as longterm_settings
    d['longterm']=longterm_settings(d['longterm'])
    for key,limit in (('budget',999),('points',5),('phase',2)):
        if isinstance(d[key],bool) or not isinstance(d[key],int) or not 0<=d[key]<=limit:raise ValueError(f'{key}必须为0–{limit}的整数')
    if d['objective'] not in ('efficiency','expected_gain','probability','target_gain','target_probability') or not isinstance(d['respect_priority'],bool):raise ValueError('推荐目标或优先级选项无效')
    if not isinstance(d['metadata'],dict) or len(d['metadata'])>3000:raise ValueError('底子记录格式无效')
    for key,m in d['metadata'].items():
        if not re.fullmatch('[0-9a-f]{64}',key) or not isinstance(m,dict) or set(m)-{'upgrade_rolls','initial_values','defined_pair'}:raise ValueError('底子记录键或字段无效')
        if m.get('upgrade_rolls') is not None and (isinstance(m['upgrade_rolls'],bool) or m['upgrade_rolls'] not in (4,5)):raise ValueError('随机强化次数必须为4或5')
        vals=m.get('initial_values',{})
        if not isinstance(vals,dict) or set(vals)-set(ROLLS):raise ValueError('初始属性无效')
        for k,v in vals.items():
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or F(str(v)) not in ROLLS[k]:raise ValueError('初始值必须选择合法档位')
        pair=m.get('defined_pair')
        if pair is not None and (not isinstance(pair,list) or len(pair)!=2 or len(set(pair))!=2 or any(k not in ROLLS for k in pair)):raise ValueError('锁定词条必须是两个不同的副属性')
    return d


def calculate(config_directory,snapshot,demand_id,progress=lambda x:None):
    from .report import load_scan
    from .batch import apply_updates
    from .campaign import Campaign,allocate
    config=Path(config_directory);data=json.loads((config/'config.json').read_text(encoding='utf-8'))
    options=settings(data.get('dust'))
    if data['equipment']=='protected' and snapshot.get('ownership_stale'):raise ValueError('不借用模式需要新的装备归属，请重新扫描')
    pool,names,coverage=load_scan(snapshot['path'])
    from .import_inventory import imported_metadata
    imported=imported_metadata(snapshot['path'])
    if not coverage['complete']:raise ValueError('需要完整库存快照')
    if snapshot.get('updates'):pool=apply_updates(pool,json.loads(Path(snapshot['updates']).read_text(encoding='utf-8')))
    campaign=Campaign.load(config/'campaign.json')
    if not options['respect_priority']:campaign.allocation='independent'
    if demand_id not in [d.id for d in campaign.demands]:raise ValueError('需求不存在')
    index=next(i for i,d in enumerate(campaign.demands) if d.id==demand_id)
    campaign.demands=campaign.demands[:index+1] if campaign.allocation=='priority' else [campaign.demands[index]]
    row=next(r for r in allocate(pool,campaign,names)['demands'] if r['id']==demand_id)
    profile=Profile(row['effective_profile']);advisor=DustAdvisor(pool,profile)
    target=profile.data.get('resource_target_score')
    if options['objective'].startswith('target_') and target is None:raise ValueError('请先填写整套目标分数')
    candidates=[a for a in pool if a.level==20 and profile.allows(a)]
    counts=Counter(fingerprint(a) for a in candidates)
    result={'kind':'dust','status':'completed','character':profile.data['character'],'demand_name':profile.data['name'],
            'set_label':profile.data['set_label'],'options':options,'baseline':[float(advisor.inventory.baseline.lo),float(advisor.inventory.baseline.hi)],
            'reserved_count':len(profile.data['reserved_ids']),'crit_cap':profile.data['artifact_crit_rate_cap'],'target_score':target,
            'current_transitions':{str(c):transition(options['points'],options['phase'],c) for c in (1,2)},
            'inventory':[],'actions':[],'deferred':[],
            'scope':'当前一次重塑的整套提升；允许保留原结果。初始值不明时给出保守区间；三/四次保底列是条件比较，不是可以直接选用的模式。未求解全预算跨物品动态最优。'}
    for index,a in enumerate(candidates,1):
        sig=fingerprint(a);meta=imported.get(a.id,{})|options['metadata'].get(sig,{})
        item={'id':a.id,'fingerprint':sig,'name':names.get(a.id,a.id),'slot':a.slot,'main':a.main,'set_label':SET_LABELS.get(a.set_key,a.set_key),
              'special':a.special,'substats':[{'key':s.key,'value':float(s.value)} for s in a.stats]}
        result['inventory'].append(item)
        try:
            if meta and counts[sig]>1:raise ValueError('属性相同的多个物品不能共享未经核验的底子记录')
            assessed=advisor.evaluate(a,meta);item.update({k:assessed[k] for k in ('upgrade_rolls_possible','initial_bounds','initial_exact','pruned')})
            step=transition(options['points'],options['phase'],COST[a.slot]);g=str(step['guarantee'])
            for action in assessed['actions']:
                result['actions'].append({'id':a.id,'fingerprint':sig,'name':item['name'],'slot':a.slot,'set_label':item['set_label'],
                    'selected':action['selected'],'cost':COST[a.slot],'affordable':COST[a.slot]<=options['budget'],
                    'next_state':step,'initial_exact':assessed['initial_exact'],'upgrade_rolls_possible':assessed['upgrade_rolls_possible'],
                    'guarantees':action['guarantees'],**action['guarantees'][g],
                    'efficiency':action['guarantees'][g]['expected_gain_lower']/COST[a.slot]})
        except ValueError as exc:
            item['error']=str(exc);result['deferred'].append({'id':a.id,'name':item['name'],'reason':str(exc)})
        if index%10==0 or index==len(candidates):progress({'phase':'dust','done':index,'total':len(candidates),'character':result['character']})
    affordable=[a for a in result['actions'] if a['affordable']]
    objectives=[('efficiency','efficiency'),('expected_gain','expected_gain_lower'),('probability','probability_lower')]
    if target is not None:objectives += [('target_gain','target_gain_lower'),('target_probability','target_probability_lower')]
    result['rankings']={k:sorted(affordable,key=lambda a:(-a[field],a['cost']))[:12] for k,field in objectives}
    result['candidate_count']=len(candidates);result['deferred_count']=len(result['deferred'])
    if not affordable:result['message']='预算不足以进行重塑，或候选缺少必要底子信息。可补充信息后重算。'
    if options['longterm']['enabled']:
        from .longterm import calculate as forecast
        result['longterm']=forecast(pool,profile,result,options['longterm'],options['metadata'],progress)
    return result
