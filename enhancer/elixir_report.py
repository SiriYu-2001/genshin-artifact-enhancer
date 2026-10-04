"""Workbench integration for offline elixir recommendations, no game I/O."""
from copy import deepcopy
from pathlib import Path
import json
import math

from .elixir import ElixirAdvisor,DEFINITION_FOUR_LINE_PROBABILITY
from .model import Profile,SLOTS


def settings(value=None):
    if value is None:value={}
    if not isinstance(value,dict):raise ValueError('祝圣之霜设置格式无效')
    defaults={'budget':4,'remaining_by_set':{},'minimum_gain':0.,'objective':'expected_gain','respect_priority':True,'compare_dust':False,'longterm':None}
    if set(value)-set(defaults):raise ValueError('未知祝圣之霜参数；定制四词条概率固定为1/3')
    result=defaults|deepcopy(value)
    from .longterm import settings as longterm_settings
    result['longterm']=longterm_settings(result['longterm'])
    b=result['budget']
    if isinstance(b,bool) or not isinstance(b,int) or not 0<=b<=999:raise ValueError('霜预算必须为0–999的整数')
    if result['objective'] not in ('expected_gain','probability','efficiency','target_gain','target_probability'):raise ValueError('推荐目标无效')
    if not isinstance(result['compare_dust'],bool):raise ValueError('重塑对照选项无效')
    if not isinstance(result['respect_priority'],bool):raise ValueError('优先级选项无效')
    x=result['minimum_gain']
    if isinstance(x,bool) or not isinstance(x,(float,int)) or not math.isfinite(x) or not 0<=x<=1000:raise ValueError('最低提升须为0–1000的有效分数')
    quotas=result['remaining_by_set']
    from .sets import SET_LABELS
    if not isinstance(quotas,dict) or any(k not in SET_LABELS or isinstance(v,bool) or not isinstance(v,int) or v not in (0,1,2) for k,v in quotas.items()):
        raise ValueError('每套剩余次数只能为0、1或2')
    return result


def ranking(plans,objective):
    key={'probability':'probability_lower','expected_gain':'expected_gain_lower','efficiency':'expected_gain_per_elixir_lower','target_gain':'target_gain_lower','target_probability':'target_probability_lower'}[objective]
    return sorted(plans,key=lambda a:(-a[key],a['cost'],a['definitions']))


def calculate(config_directory,snapshot,demand_id,progress=lambda x:None):
    from .report import load_scan
    from .batch import apply_updates
    from .campaign import Campaign,allocate
    directory=Path(config_directory)
    config=json.loads((directory/'config.json').read_text(encoding='utf-8'))
    options=settings(config.get('elixir'))
    if demand_id not in {d['id'] for d in config['demands']}:raise ValueError('请选择当前配置中的培养目标')
    if config['equipment']=='protected' and snapshot.get('ownership_stale'):
        raise ValueError('装备归属已过期，不借用模式需要重新扫描')
    pool,names,coverage=load_scan(snapshot['path'])
    if not coverage['complete']:raise ValueError('需要完整库存快照')
    if snapshot.get('updates'):pool=apply_updates(pool,json.loads(Path(snapshot['updates']).read_text(encoding='utf-8')))
    campaign=Campaign.load(directory/'campaign.json')
    if not options['respect_priority']:campaign.allocation='independent'
    index=next(i for i,d in enumerate(campaign.demands) if d.id==demand_id)
    campaign.demands=campaign.demands[:index+1] if campaign.allocation=='priority' else [campaign.demands[index]]
    allocation=allocate(pool,campaign,names)
    row=next(d for d in allocation['demands'] if d['id']==demand_id)
    profile=Profile(row['effective_profile'])
    target=profile.data.get('resource_target_score')
    if options['objective'].startswith('target_') and target is None:raise ValueError('请先填写整套目标分数')
    if row['status']!='ready':raise ValueError('当前约束下还没有完整+20基准配装，暂不能计算提升')
    advisor=ElixirAdvisor(pool,profile)
    remaining=options['remaining_by_set'].get(profile.data['set_key'],2)
    result={'kind':'elixir','status':'completed','character':profile.data['character'],
            'demand_name':profile.data['name'],'demand_id':demand_id,'set_label':profile.data['set_label'],
            'options':options,'remaining':remaining,'baseline':[float(advisor.inventory.baseline.lo),float(advisor.inventory.baseline.hi)],
            'reserved_count':len(profile.data['reserved_ids']),'p_four':'1/3','p_three':'2/3',
            'scope':'当前库存、完整4+1重新配装、定制后+20；不包含未来刷取。双件为固定候选模拟，重塑替代单独对照，不是霜尘联合动态最优。',
            'configuration':{'weights':profile.data['weights'],'main_stats':profile.data['main_stats'],
                             'crit_cap':profile.data['artifact_crit_rate_cap'],'equipment':config['equipment'],
                             'allocation':campaign.allocation,'target_score':target},'single_actions':[],'plans':[],'rankings':{}}
    def compare_resources(singles):
        if not options['compare_dust']:return
        from .dust_report import calculate as dust_calculate,settings as dust_settings
        if dust_settings(config.get('dust'))['respect_priority']!=options['respect_priority']:raise ValueError('霜尘对照需要两页优先级设置一致')
        dust=dust_calculate(directory,snapshot,demand_id,progress)
        # Compare actual one-operation alternatives, not elixir two-craft plans vs one reshape.
        metric=options['objective'] if options['objective']!='efficiency' else 'expected_gain'
        choices=dust['rankings'].get(metric,[])
        result['resource_comparison']={'dust_options':dust['options'],'deferred_count':dust['deferred_count'],
            'elixir':ranking(singles,metric)[:3],'dust':choices[:3],
            'note':'同一库存、角色与优先级下的单次机会对照。霜与尘分别计成本，不默认1:1汇率。初值未知的重塑结果是范围；底子无法提升时优先考虑换胚。'}
    if not remaining or options['budget']==0:
        result['message']='该套装本期次数为0，请等待刷新或修正填写的次数。' if not remaining else '霜预算为0，本次保留材料。'
        compare_resources([])
        if options['longterm']['enabled']:
            from .longterm import calculate as forecast
            result['longterm']=forecast(pool,profile,result,options['longterm'],progress=progress)
        return result
    actions=list(advisor.actions());progress({'phase':'elixir','done':0,'total':len(actions),'character':result['character']})
    for i,(slot,main,selected) in enumerate(actions,1):
        action=advisor.evaluate(slot,main,selected,DEFINITION_FOUR_LINE_PROBABILITY,options['minimum_gain'])
        result['single_actions'].append(action)
        if i%12==0 or i==len(actions):progress({'phase':'elixir','done':i,'total':len(actions),'character':result['character']})
    singles=[dict(a,definitions=1,actions=[deepcopy(a)],method='enumerated') for a in result['single_actions'] if a['cost']<=options['budget']]
    plans=list(singles)
    if remaining>=2 and options['budget']>=2:
        from .elixir_plans import compare
        # Keep the documented fixed shortlist, not a claim of full policy optimality.
        shortlist=[max((a for a in result['single_actions'] if a['slot']==slot),key=lambda a:a['expected_gain_lower']) for slot in SLOTS]
        progress({'phase':'elixir_pairs','character':result['character']})
        for pair in compare(profile,pool,shortlist,budget=options['budget'],minimum_gain=options['minimum_gain']):
            lo,hi=pair['metrics']['lo'],pair['metrics']['hi']
            plans.append(dict(pair,method='sampled_fixed_pair',probability_lower=lo['probability'],probability_upper=hi['probability'],
                expected_gain_lower=lo['expected_gain'],expected_gain_upper=hi['expected_gain'],
                expected_gain_per_elixir_lower=lo['expected_gain']/pair['cost']))
            if target is not None:plans[-1].update(target_probability_lower=lo['target_probability'],target_probability_upper=hi['target_probability'],target_gain_lower=lo['target_gain'],target_gain_upper=hi['target_gain'])
    result['plans']=plans
    objectives=['expected_gain','probability','efficiency']+(['target_gain','target_probability'] if target is not None else [])
    result['rankings']={objective:ranking(plans,objective)[:8] for objective in objectives}
    compare_resources(singles)
    if options['longterm']['enabled']:
        from .longterm import calculate as forecast
        result['longterm']=forecast(pool,profile,result,options['longterm'],progress=progress)
    return result
