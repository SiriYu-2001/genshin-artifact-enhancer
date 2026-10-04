"""Offline finite-horizon resource advice, paired with natural farming.

Policy: farm first, then perform ONE fixed action chosen from today's inventory.
All generated drops are evaluated at +20 (unlimited ordinary training), not a
simulation of the enhancement executor's probability stopping policy.
Float Pareto DP solves 4+1 exactly within each sampled inventory; no LLM/I/O.
"""
from copy import deepcopy
import hashlib
import math
import numpy as np

from .model import SLOTS, ROLLS, MEANS
from .capped import components, main_crit
from .elixir import TYPE_WEIGHT
from .elixir_plans import sample_candidate
from .dust import infer_bases

# Highest-level ordinary artifact domain. No strongbox, event or resin refresh.
MAIN_PROBS = {
    'flower': {'hp': 1.}, 'plume': {'atk': 1.},
    'sands': {'hp_': .2668, 'atk_': .2666, 'def_': .2666, 'enerRech_': .1, 'eleMas': .1},
    'goblet': {'hp_': .1925, 'atk_': .1925, 'def_': .19, 'eleMas': .025,
               **{k+'_dmg_': .05 for k in ('physical','pyro','hydro','cryo','electro','anemo','geo','dendro')}},
    'circlet': {'hp_': .22, 'atk_': .22, 'def_': .22, 'critRate_': .1, 'critDMG_': .1, 'heal_': .1, 'eleMas': .04}}


def settings(value=None):
    defaults = {'enabled': False, 'days': 90, 'daily_resin': 180, 'samples': 512,
                'seed': 20261003, 'five_star_per_20': 1.065}
    if value is None: value = {}
    if not isinstance(value, dict) or set(value)-set(defaults): raise ValueError('长期预测参数无效')
    out = defaults | deepcopy(value)
    if not isinstance(out['enabled'], bool): raise ValueError('长期预测开关无效')
    for key, upper in (('days',365), ('daily_resin',1440), ('seed',2**32-1)):
        if isinstance(out[key],bool) or not isinstance(out[key],int) or not 0<=out[key]<=upper:
            raise ValueError(f'{key}必须是0–{upper}的整数')
    if not isinstance(out['samples'],int) or isinstance(out['samples'],bool) or out['samples'] not in (128,512,2048): raise ValueError('模拟次数须为128、512或2048')
    r = out['five_star_per_20']
    if isinstance(r,bool) or not isinstance(r,(int,float)) or not math.isfinite(r) or not 1<=r<=2:
        raise ValueError('每20树脂五星产出须在1–2之间')
    return out


def seed_for(seed, *parts):
    return int.from_bytes(hashlib.sha256(repr((seed,parts)).encode()).digest()[:8], 'little')


def pareto(rows, cap):
    """Rows=(crit, weighted other substats, main crit). Group by main crit."""
    rows = np.asarray(rows,dtype=float).reshape(-1,3)
    if not len(rows): return rows
    result=[]
    for main in np.unique(rows[:,2]):
        a=rows[rows[:,2]==main].copy(); a[:,0]=np.minimum(a[:,0], max(0.,cap-main))
        order=np.lexsort((-a[:,1],-a[:,0])); a=a[order]
        best=-np.inf
        for row in a:
            if row[1]>best:
                result.append(row); best=row[1]
    return np.asarray(result).reshape(-1,3)


class Inventory:
    """Small numeric projection of the existing exact-rational inventory DP."""
    def __init__(self, base, future, cap, weight):
        self.cap=cap; self.weight=weight; self.cache={}; self.buckets={}
        self.base=base;self.future=future
        for slot in SLOTS:
            for target in (False,True):
                rows=base[slot,target]
                extra=future[slot,target]
                self.buckets[slot,target]=pareto(np.concatenate((rows,extra)),cap)

    def frontier(self, slot=None, target=True):
        key=slot,target
        if key in self.cache:return self.cache[key]
        result=[]
        for off in SLOTS:
            if slot is not None and not target and slot!=off:continue
            rows=np.zeros((1,3))
            for s in SLOTS:
                if s==slot:continue
                bucket=self.buckets[s,s!=off]
                rows=pareto((rows[:,None,:]+bucket[None,:,:]).reshape(-1,3),self.cap)
                if not len(rows):break
            result.extend(rows)
        answer=pareto(result,self.cap);self.cache[key]=answer
        return answer

    def new_frontier(self):
        """Require at least one generated item, excluding an unchanged old build.

        Otherwise F_hi(I+D)-F_lo(I) spuriously reports a possible improvement
        even when every drop is useless, due solely to existing OCR rounding.
        """
        result=[]
        for off in SLOTS:
            old=np.zeros((1,3));new=np.empty((0,3))
            for s in SLOTS:
                k=s,s!=off
                old_bucket=pareto(self.base[k],self.cap)
                fresh_bucket=pareto(self.future[k],self.cap)
                via_new=(new[:,None,:]+self.buckets[k][None,:,:]).reshape(-1,3)
                first_new=(old[:,None,:]+fresh_bucket[None,:,:]).reshape(-1,3)
                new=pareto(np.concatenate((via_new,first_new)),self.cap)
                old=pareto((old[:,None,:]+old_bucket[None,:,:]).reshape(-1,3),self.cap)
            result.extend(new)
        return pareto(result,self.cap)

    def score(self, rows):
        if not len(rows):return -np.inf
        return float(np.max(rows[:,1]+self.weight*np.minimum(rows[:,0],np.maximum(0,self.cap-rows[:,2]))))

    def candidate(self, slot, target, cr, other, main):
        points=self.frontier(slot,target)
        if not len(points):return -np.inf
        return float(np.max(points[:,1]+other+self.weight*np.minimum(points[:,0]+cr,np.maximum(0,self.cap-points[:,2]-main))))

    def candidates(self,slot,target,rows):
        points=self.frontier(slot,target)
        if not len(points) or not len(rows):return -np.inf
        return float(np.max(points[:,1,None]+rows[None,:,1]+self.weight*
                      np.minimum(points[:,0,None]+rows[None,:,0],np.maximum(0,self.cap-points[:,2,None]-rows[None,:,2]))))


def project(pool, profile, bound):
    base={(s,t):[] for s in SLOTS for t in (False,True)}
    for a in pool:
        if a.level!=20 or not profile.allows(a):continue
        cr,other=components(a,profile)
        row=[float(getattr(cr,bound)),float(getattr(other,bound)),float(main_crit(a))]
        base[a.slot,False].append(row)
        if a.set_key==profile.data['set_key']:base[a.slot,True].append(row)
    return {k:np.asarray(v).reshape(-1,3) for k,v in base.items()}


def sample_natural(profile,n,rng):
    """Sample domain drops incl both sets, all slots/mains and weighted types."""
    slot=rng.integers(0,5,n); target=rng.random(n)<.5
    cr=np.zeros(n);other=np.zeros(n);maincr=np.zeros(n);legal=np.zeros(n,dtype=bool)
    keys=list(ROLLS);tiers=np.array([[float(x) for x in ROLLS[k]] for k in keys])
    coef=np.array([float(profile.weight(k)/MEANS[k]) if k!='critRate_' else 0 for k in keys])
    for si,s in enumerate(SLOTS):
        positions=np.flatnonzero(slot==si); mains=list(MAIN_PROBS[s]); ps=list(MAIN_PROBS[s].values())
        mainchoices=rng.choice(len(mains),len(positions),p=ps)
        for mi,main in enumerate(mains):
            idx=positions[mainchoices==mi]
            if main not in profile.data['main_stats'][s] or not len(idx):continue
            m=len(idx);legal[idx]=True; maincr[idx]=31.1 if main=='critRate_' else 0
            weights=np.tile([TYPE_WEIGHT[k] if k!=main else 0 for k in keys],(m,1)).astype(float)
            chosen=np.empty((m,4),dtype=int)
            for col in range(4):
                cumulative=np.cumsum(weights,axis=1)
                selected=np.sum(rng.random(m)[:,None]*cumulative[:,-1,None]>=cumulative,axis=1)
                chosen[:,col]=selected;weights[np.arange(m),selected]=0
            vals=tiers[chosen,rng.integers(0,4,(m,4))]
            rolls=4+(rng.random(m)<.2)
            for step in range(5):
                hit=rng.integers(0,4,m);inc=tiers[chosen[np.arange(m),hit],rng.integers(0,4,m)]
                vals[np.arange(m),hit]+=inc*(rolls>step)
            cr[idx]=np.sum(vals*(chosen==keys.index('critRate_')),axis=1)
            other[idx]=np.sum(vals*coef[chosen],axis=1)
    return slot,target,np.column_stack((cr,other,maincr)),legal


def summary(values, probability=False):
    """MC uncertainty, separate from OCR/base uncertainty. Wilson for Bernoulli."""
    v=np.asarray(values,dtype=float); n=len(v); mean=float(v.mean()); z=1.96
    if probability:
        den=1+z*z/n; centre=(mean+z*z/(2*n))/den
        half=z*math.sqrt(mean*(1-mean)/n+z*z/(4*n*n))/den
        lo,hi=max(0.,centre-half),min(1.,centre+half)
    else:
        # No successes provide no variance-based information about a rare tail.
        # Do not show [0,0] as if it were a valid confidence interval on its mean.
        if not np.any(v):return {'mean':mean,'mc95':None}
        half=z*float(v.std(ddof=1))/math.sqrt(n);lo,hi=mean-half,mean+half
    return {'mean':mean,'mc95':[lo,hi]}


def interval_summary(lower, upper, probability=False):
    a,b=summary(lower,probability),summary(upper,probability)
    interval=[a['mc95'][0],b['mc95'][1]] if a['mc95'] is not None and b['mc95'] is not None else None
    return {'estimate':[a['mean'],b['mean']], 'mc95':interval}


def dust_samples(profile,artifact,pair,metadata,g,n,seed):
    """Retain separate possible roll-count histories; no prior over unknown bases."""
    out=[];keys=tuple(pair)+tuple(s.key for s in artifact.stats if s.key not in pair)
    tiers=np.array([[float(v) for v in ROLLS[k]] for k in keys])
    coef=np.array([float(profile.weight(k)/MEANS[k]) if k!='critRate_' else 0 for k in keys])
    for rolls,bases in infer_bases(artifact,metadata):
        rng=np.random.default_rng(seed_for(seed,rolls));v=np.zeros((n,4));hits=np.zeros(n,int)
        for step in range(rolls):
            forced=rolls-step<=g-hits
            hit=rng.integers(0,4,n);hit[forced]=rng.integers(0,2,forced.sum())
            v[np.arange(n),hit]+=tiers[hit,rng.integers(0,4,n)];hits+=hit<2
        bounds=[]
        for bound in ('lo','hi'):
            values=v+np.array([float(getattr(bases[k],bound)) for k in keys])
            cr=values[:,keys.index('critRate_')] if 'critRate_' in keys else np.zeros(n)
            bounds.append((cr,np.sum(values*coef,axis=1)))
        out.append(bounds)
    return out


def shortlist(result):
    """Explicit finite shortlist: top 3 per slot per immediate metric, union."""
    candidates=(result['single_actions'] if result['kind']=='elixir' else result['actions'])
    candidates=[a for a in candidates if a['cost']<=result['options']['budget']]
    if result['kind']=='elixir' and result['remaining']==0:candidates=[]
    fields=['expected_gain_lower','probability_lower','expected_gain_upper']
    if candidates and 'target_gain_lower' in candidates[0]:fields+=['target_gain_lower','target_probability_lower']
    kept={}
    for slot in SLOTS:
        rows=[a for a in candidates if a['slot']==slot]
        for field in fields:
            for a in sorted(rows,key=lambda a:-a[field])[:3]:
                key=(a.get('id'),a['slot'],a.get('main'),tuple(a['selected']))
                kept[key]=a
    return list(kept.values()),len(candidates)


def calculate(pool,profile,result,options,metadata=None,progress=lambda x:None):
    """Full future-inventory reoptimization, horizon-conditioned single actions."""
    options=settings(options);n=options['samples'];seed=options['seed'];metadata=metadata or {}
    actions,total_actions=shortlist(result)
    cap=float(profile.data['artifact_crit_rate_cap']);w=float(profile.weight('critRate_')/MEANS['critRate_'])
    bases=[project(pool,profile,b) for b in ('lo','hi')]
    empty={k:np.empty((0,3)) for k in bases[0]}
    initial=[Inventory(b,empty,cap,w) for b in bases]
    baseline=[inv.score(inv.frontier()) for inv in initial]
    if not all(math.isfinite(v) for v in baseline):raise ValueError('长期预测需要完整满级基准')
    days=sorted(set((0,options['days']//2,options['days'])))
    runs=[d*options['daily_resin']//20 for d in days]
    rng=np.random.default_rng(seed_for(seed,'drop_counts'))
    counts=np.zeros((n,len(days)),int)
    for j,r in enumerate(runs):
        previous=runs[j-1] if j else 0
        counts[:,j]=(counts[:,j-1] if j else 0)+(r-previous)+rng.binomial(r-previous,options['five_star_per_20']-1,n)
    lookup={a.id:a for a in pool};draws=[]
    for a in actions:
        actionseed=seed_for(seed,'action',result['kind'],a.get('id'),a['slot'],a.get('main'),tuple(a['selected']))
        if result['kind']=='elixir':
            samples=sample_candidate(profile,a,n,actionseed)
            draws.append([[samples,samples]])
        else:
            artifact=lookup[a['id']]
            draws.append(dust_samples(profile,artifact,a['selected'],metadata.get(a['fingerprint'],{}),a['next_state']['guarantee'],n,actionseed))
    # bounds x horizon x paths; action deltas use paired future worlds.
    natural=np.zeros((2,len(days),n)); slotgain=np.zeros((2,len(days),5,n))
    gains=np.zeros((len(actions),2,len(days),n));targetg=np.zeros_like(gains)
    target=profile.data.get('resource_target_score');min_gain=result['options'].get('minimum_gain',0.)
    for i in range(n):
        slots,targets,points,legal=sample_natural(profile,int(counts[i,-1]),np.random.default_rng(seed_for(seed,'world',i)))
        for j in range(len(days)):
            take=int(counts[i,j]);future={}
            for si,s in enumerate(SLOTS):
                for t in (False,True):
                    mask=(slots[:take]==si)&legal[:take]
                    if t:mask &=targets[:take]
                    future[s,t]=points[:take][mask]
            invs=[Inventory(b,future,cap,w) for b in bases]
            scores=[inv.score(inv.frontier()) for inv in invs]
            if np.any(legal[:take]):
                forced=[inv.score(inv.new_frontier()) for inv in invs]
                natural[0,j,i]=max(0,forced[0]-baseline[1]);natural[1,j,i]=max(0,forced[1]-baseline[0])
            # Slot opportunity: one new drop + today's other pieces, not additive contributions.
            for si,s in enumerate(SLOTS):
                if len(future[s,False]):
                    mask=(slots[:take]==si)&legal[:take]
                    ss=[max(inv.candidates(s,t,points[:take][mask&(targets[:take]==t)]) for t in (False,True)) for inv in initial]
                    slotgain[0,j,si,i]=max(0,ss[0]-baseline[1]);slotgain[1,j,si,i]=max(0,ss[1]-baseline[0])
            for ai,a in enumerate(actions):
                if result['kind']=='elixir':main=31.1 if a['main']=='critRate_' else 0;is_target=True
                else:
                    art=lookup[a['id']];main=float(main_crit(art));is_target=art.set_key==profile.data['set_key']
                cases=[[],[]];tcases=[[],[]]
                for hypothesis in draws[ai]:
                    for bi in (0,1):
                        cr,other=hypothesis[bi]
                        forced=invs[bi].candidate(a['slot'],is_target,float(cr[i]),float(other[i]),main)
                        # Dust: all complements omit this slot, so the old and new versions
                        # can never coexist. Retention happens AFTER farming, with observed D.
                        old=scores[1-bi]
                        cases[bi].append(max(0,forced-old))
                        if target is not None:tcases[bi].append(max(0,min(forced,target)-min(old,target)))
                gains[ai,0,j,i]=min(cases[0]);gains[ai,1,j,i]=max(cases[1])
                if target is not None:
                    targetg[ai,0,j,i]=min(tcases[0]);targetg[ai,1,j,i]=max(tcases[1])
        if (i+1)%16==0 or i+1==n:progress({'phase':'longterm','done':i+1,'total':n,'character':profile.data['character']})
    horizons=[]
    for j,d in enumerate(days):
        records=[]
        for ai,a in enumerate(actions):
            low,high=gains[ai,:,j,:]
            rec={k:a[k] for k in ('slot','cost','selected','main','id','name','next_state') if k in a}
            rec.update(expected_gain=interval_summary(low,high),efficiency=interval_summary(low/a['cost'],high/a['cost']),
                       probability=interval_summary(low>min_gain+1e-10,high>min_gain+1e-10,True))
            if target is not None:rec['target_gain']=interval_summary(*targetg[ai,:,j,:])
            records.append(rec)
        ranks={key:sorted(records,key=lambda a:-a[key]['estimate'][0]) for key in ('expected_gain','efficiency','probability')}
        if target is not None:ranks['target_gain']=sorted(records,key=lambda a:-a['target_gain']['estimate'][0])
        natural_probability=interval_summary(natural[0,j,:]>1e-10,natural[1,j,:]>1e-10,True)
        if runs[j]==0:natural_probability={'estimate':[0.,0.],'mc95':[0.,0.]}
        horizons.append({'days':d,'expected_drops':runs[j]*options['five_star_per_20'],
                         'natural_gain':interval_summary(*natural[:,j,:]),
                         'natural_probability':natural_probability,
                         'slots':[{'slot':s,'probability':interval_summary(slotgain[0,j,si,:]>1e-10,slotgain[1,j,si,:]>1e-10,True),
                                   'gain':interval_summary(*slotgain[:,j,si,:])} for si,s in enumerate(SLOTS)],
                         'rankings':{k:v[:8] for k,v in ranks.items()}})
    return {'method':'paired_future_inventory_mc_v1','options':options,'baseline':baseline,'horizons':horizons,
            'shortlist_count':len(actions),'eligible_action_count':total_actions,
            'scope':'先刷取，再执行一次当前候选动作；完整4+1重新优化。新掉落均按+20潜力评估，不模拟5%序贯停手。只模拟目标秘境两套等概率掉落；另一套可作散件。高优先级当前占用保持预留。尘期间不另行消耗，期末才决定保留新旧结果。',
            'limits':'每部位按当前期望、成功率、乐观收益等各取前三并集；不是全部动作或多次预算动态最优。未知底子使用保守包络，区间非其概率分布。MC95为单项近似抽样区间，不含机制、模型或筛选误差；零命中不等于不可能。未计入未来获得的新尘底子、未来霜尘收入、等待期使用价值、合成台或其他秘境散件。'}
