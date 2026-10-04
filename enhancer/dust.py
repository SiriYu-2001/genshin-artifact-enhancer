"""Dust reshaping: fixed bases, six selectable pairs, global 3/3/4 pity.

Pure computation only. Unknown initial values produce conservative bounds,
never a guessed mean initial roll. Old and reshaped results are alternatives
for the same physical artifact, not two inventory items.
"""
from fractions import Fraction as F
from itertools import combinations,product
from functools import lru_cache
import numpy as np

from .model import Bounds,ROLLS,MEANS
from .capped import CappedInventory,main_crit
from .elixir import upgrade_counts,roll_sums,other_distribution,tail_metrics,target_metrics

COST={'flower':1,'plume':1,'sands':2,'goblet':2,'circlet':2}


def transition(points,phase,cost):
    if any(isinstance(v,bool) or not isinstance(v,int) for v in (points,phase,cost)) or points not in range(6) or phase not in range(3) or cost not in (1,2):
        raise ValueError('Invalid dust pity state')
    total=points+cost
    if total<6:return {'guarantee':2,'points':total,'phase':phase,'triggered':False}
    return {'guarantee':4 if phase==2 else 3,'points':total-6,'phase':(phase+1)%3,'triggered':True}


@lru_cache(maxsize=10000)
def stat_bases(key,value,exact=False,initial=None):
    tolerance=F(1,100000) if exact else (F(1,20) if key.endswith('_') else F(1,2))+F(1,10000)
    rows={}
    for hits in range(6):
        support,_=roll_sums(key,hits)
        bases=[base for base in ROLLS[key] if (initial is None or base==initial) and
               any(abs(base+F(total,100)-value)<=tolerance for total,n in support)]
        if bases:rows[hits]=tuple(bases)
    return rows


def infer_bases(artifact,metadata=None):
    artifact.validate();metadata=metadata or {}
    if artifact.level!=20:raise ValueError('重塑只适用于+20五星圣遗物')
    keys=tuple(s.key for s in artifact.stats)
    initials=metadata.get('initial_values',{})
    if not isinstance(initials,dict) or set(initials)-set(keys):raise ValueError('初始属性字段与当前物品不符')
    initial={k:F(str(v)) for k,v in initials.items()}
    if any(v not in ROLLS[k] for k,v in initial.items()):raise ValueError('初始值必须是该属性的合法单次档位')
    rolls=metadata.get('upgrade_rolls')
    if rolls is not None and (isinstance(rolls,bool) or rolls not in (4,5)):raise ValueError('随机强化次数只能是4或5')
    possibilities=[stat_bases(s.key,s.value,s.exact,initial.get(s.key)) for s in artifact.stats]
    collected={}
    for counts in product(*(p.keys() for p in possibilities)):
        n=sum(counts)
        if n not in (4,5) or (rolls is not None and n!=rolls):continue
        bag=collected.setdefault(n,{k:set() for k in keys})
        for i,(k,hits) in enumerate(zip(keys,counts)):bag[k].update(possibilities[i][hits])
    if not collected:raise ValueError('显示属性、初始值或强化次数无法组成合法历史，请核验数据')
    return tuple((n,{k:Bounds(min(v),max(v)) for k,v in values.items()}) for n,values in sorted(collected.items()))


class DustAdvisor:
    def __init__(self,pool,profile):
        if profile.data.get('set_requirement')!=4 or profile.data.get('normalization')!='mean_roll':raise ValueError('仅支持平均词条评分与4+1')
        self.profile=profile;self.inventory=CappedInventory(pool,profile)
        if self.inventory.baseline is None:raise ValueError('缺少完整+20基准配装')

    def evaluate(self,artifact,metadata=None):
        metadata=metadata or {}
        hypotheses=infer_bases(artifact,metadata)
        keys=tuple(s.key for s in artifact.stats)
        selected=metadata.get('defined_pair')
        if selected is not None and (not isinstance(selected,list) or len(selected)!=2 or len(set(selected))!=2 or set(selected)-set(keys)):
            raise ValueError('定制件锁定词条必须是当前物品中的两个不同副属性')
        if artifact.special!='ordinary' and selected is None:raise ValueError('定制状态未知，或定制件缺少锁定词条；请核验来源数据及游戏自动选中的两条')
        pairs=[tuple(selected)] if artifact.special!='ordinary' else list(combinations(keys,2))
        complements={b:self.inventory.complement(artifact,b) for b in ('lo','hi')}
        if not complements['lo'].points:raise ValueError('该物品无法组成满足约束的4+1配装')
        best_hit=max(self.profile.weight(k)*max(ROLLS[k])/MEANS[k] for k in keys)
        upper=max(sum(b.hi*self.profile.weight(k)/MEANS[k] for k,b in bases.items() if k!='critRate_')+
                  complements['hi'].query(bases.get('critRate_',Bounds(F(0),F(0))).hi,main_crit(artifact))+n*best_hit
                  for n,bases in hypotheses)
        pruned=upper<=self.inventory.baseline.lo
        responses={}
        def thresholds(n,bases,bound,count):
            key=n,bound,count
            if key not in responses:
                support,den=roll_sums('critRate_',count)
                cr=getattr(bases.get('critRate_',Bounds(F(0),F(0))),bound)
                other=sum(getattr(v,bound)*self.profile.weight(k)/MEANS[k] for k,v in bases.items() if k!='critRate_')
                baseline=self.inventory.baseline.hi if bound=='lo' else self.inventory.baseline.lo
                response=np.array([float(baseline-other-complements[bound].query(cr+F(v,100),main_crit(artifact))) for v,_ in support])
                responses[key]=response,np.array([num/den for _,num in support])
            return responses[key]
        actions=[]
        target=self.profile.data.get('resource_target_score')
        for pair in pairs:
            ordered=pair+tuple(k for k in keys if k not in pair)
            guarantees={}
            for guarantee in (2,3,4):
                if pruned:
                    guarantees[str(guarantee)]={'probability_lower':0.,'probability_upper':0.,'expected_gain_lower':0.,'expected_gain_upper':0.}
                    if target is not None:guarantees[str(guarantee)].update(target_probability_lower=float(self.inventory.baseline.lo>=target),target_probability_upper=float(self.inventory.baseline.hi>=target),target_gain_lower=0.,target_gain_upper=0.)
                    continue
                cases=[]
                for n,bases in hypotheses:
                    total=np.zeros(8)
                    for counts,prob in upgrade_counts(n,guarantee):
                        cr_count=counts[ordered.index('critRate_')] if 'critRate_' in ordered else 0
                        spec=tuple(sorted((k,h,self.profile.weight(k)) for k,h in zip(ordered,counts) if k!='critRate_' and self.profile.weight(k)>0))
                        values,tail,moment=other_distribution(spec)
                        for i,bound in enumerate(('lo','hi')):
                            cuts,probs=thresholds(n,bases,bound,cr_count)
                            p,g=tail_metrics(values,tail,moment,cuts)
                            total[i]+=float(prob)*float(np.dot(probs,p));total[2+i]+=float(prob)*float(np.dot(probs,g))
                            if target is not None:
                                base=float(self.inventory.baseline.hi if bound=='lo' else self.inventory.baseline.lo)
                                pg,tg=target_metrics(values,tail,moment,cuts,float(target)-base)
                                old=float(self.inventory.baseline.lo if bound=='lo' else self.inventory.baseline.hi)
                                total[4+i]+=float(prob)*(1. if old>=target else float(np.dot(probs,pg)))
                                total[6+i]+=float(prob)*float(np.dot(probs,tg))
                    cases.append(total)
                guarantees[str(guarantee)]={'probability_lower':min(x[0] for x in cases),'probability_upper':max(x[1] for x in cases),
                    'expected_gain_lower':min(x[2] for x in cases),'expected_gain_upper':max(x[3] for x in cases)}
                if target is not None:guarantees[str(guarantee)].update(target_probability_lower=min(x[4] for x in cases),target_probability_upper=max(x[5] for x in cases),target_gain_lower=min(x[6] for x in cases),target_gain_upper=max(x[7] for x in cases))
            actions.append({'selected':list(pair),'guarantees':guarantees})
        return {'pruned':pruned,'upgrade_rolls_possible':[n for n,_ in hypotheses],
                'initial_bounds':[{'upgrade_rolls':n,'values':{k:[float(b.lo),float(b.hi)] for k,b in values.items()}} for n,values in hypotheses],
                'initial_exact':len(hypotheses)==1 and all(v.lo==v.hi for v in hypotheses[0][1].values()),'actions':actions}
