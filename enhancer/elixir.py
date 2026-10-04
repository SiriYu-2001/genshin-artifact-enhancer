"""Read-only, single-definition expected improvement over a capped 4+1 inventory.

Discrete outcomes are exhaustively summed, not sampled. Integer roll-count DP
and integer-cent tier convolutions; final probability/moment sums use float64.
Mechanics and unconfirmed creation assumptions are documented separately.
This module has no game input, crafting or extraction operations.
"""
from collections import Counter
from fractions import Fraction as F
from functools import lru_cache
from itertools import combinations

import numpy as np

from .model import Artifact, ROLLS, MEANS, SLOTS
from .capped import CappedInventory, main_crit

COST = dict(zip(SLOTS, (1, 1, 2, 4, 3)))
# Definition source only. Domain drops have a separate 1/5 four-line rate.
DEFINITION_FOUR_LINE_PROBABILITY = F(1,3)
TYPE_WEIGHT = {k: 6 if k in ('hp','atk','def') else 3 if k in ('critRate_','critDMG_') else 4 for k in ROLLS}


def remaining_types(main, selected):
    if len(selected)!=2 or len(set(selected))!=2 or any(k not in ROLLS or k==main for k in selected):
        raise ValueError('Definition requires two distinct legal substats')
    keys=[k for k in ROLLS if k!=main and k not in selected]
    total=sum(TYPE_WEIGHT[k] for k in keys)
    return tuple((a,b,F(TYPE_WEIGHT[a]*TYPE_WEIGHT[b],total)*
                 (F(1,total-TYPE_WEIGHT[a])+F(1,total-TYPE_WEIGHT[b])))
                 for a,b in combinations(keys,2))


@lru_cache(maxsize=12)
def upgrade_counts(rolls, guarantee=2):
    """Indices 0 and 1 are selected; force only when remaining hits require it."""
    if not 0<=guarantee<=rolls<=5:raise ValueError('Invalid guarantee or roll count')
    states=Counter({(0,0,0,0):1})
    for step in range(rolls):
        nxt=Counter()
        for counts,number in states.items():
            forced=rolls-step<=guarantee-counts[0]-counts[1]
            for i in range(2 if forced else 4):
                updated=list(counts);updated[i]+=1
                nxt[tuple(updated)]+=number*(2 if forced else 1)
        states=nxt
    assert sum(states.values())==4**rolls
    return tuple((counts,F(number,4**rolls)) for counts,number in states.items())


@lru_cache(maxsize=70)
def roll_sums(key, count):
    if key not in ROLLS or not 0<=count<=6:raise ValueError('Invalid roll sum')
    states=Counter({0:1})
    for _ in range(count):
        nxt=Counter()
        for total,n in states.items():
            for value in ROLLS[key]:nxt[total+int(value*100)]+=n
        states=nxt
    return tuple(sorted(states.items())),4**count


@lru_cache(maxsize=1024)
def other_distribution(spec):
    values=np.array([0.]);probs=np.array([1.])
    for key,count,weight in spec:
        support,den=roll_sums(key,count)
        v=np.array([x/100 for x,n in support])*float(weight/MEANS[key])
        p=np.array([n/den for x,n in support])
        values=(values[:,None]+v[None,:]).ravel()
        probs=(probs[:,None]*p[None,:]).ravel()
    order=np.argsort(values);values=values[order];probs=probs[order]
    tail=np.r_[np.cumsum(probs[::-1])[::-1],0.]
    moment=np.r_[np.cumsum((probs*values)[::-1])[::-1],0.]
    return values,tail,moment


def tail_metrics(values,tail,moment,thresholds,minimum_gain=0.):
    """P(delta > minimum_gain), E[max(delta,0)]; retain gains below the goal."""
    indices=np.searchsorted(values,thresholds+minimum_gain+1e-10,side='right')
    positive=np.searchsorted(values,thresholds+1e-10,side='right')
    return tail[indices],np.maximum(0,moment[positive]-thresholds*tail[positive])


def target_metrics(values,tail,moment,thresholds,headroom):
    indices=np.searchsorted(values,thresholds+headroom-1e-10,side='left')
    _,gain=tail_metrics(values,tail,moment,thresholds)
    _,excess=tail_metrics(values,tail,moment,thresholds+max(0,headroom))
    return tail[indices],np.maximum(0,gain-excess)


class ElixirAdvisor:
    def __init__(self,pool,profile):
        if profile.data.get('set_requirement')!=4 or profile.data.get('normalization')!='mean_roll':
            raise ValueError('Elixir advisor currently requires mean-roll scoring and 4+1')
        if any(profile.weight(k)<0 for k in ROLLS):raise ValueError('Negative weights unsupported')
        self.profile=profile
        self.inventory=CappedInventory(pool,profile)
        if self.inventory.baseline is None:raise ValueError('Complete mature baseline required')
        self.responses={}

    def thresholds(self,slot,main,bound,cr_count):
        key=slot,main,bound,cr_count
        if key not in self.responses:
            artifact=Artifact('__proposed_definition__',self.profile.data['set_key'],slot,main,20,())
            complement=self.inventory.complement(artifact,bound)
            if not complement.points:raise ValueError('No feasible complement')
            support,den=roll_sums('critRate_',cr_count)
            baseline=self.inventory.baseline.hi if bound=='lo' else self.inventory.baseline.lo
            thresholds=np.array([float(baseline-complement.query(F(cents,100),main_crit(artifact))) for cents,n in support])
            self.responses[key]=thresholds,np.array([n/den for cents,n in support])
        return self.responses[key]

    def conditional(self,slot,main,selected,rolls,minimum_gain=0.):
        total=np.zeros(8) # improvement, raw gain, target attainment, capped gain
        target=self.profile.data.get('resource_target_score')
        for a,b,type_probability in remaining_types(main,selected):
            keys=tuple(selected)+(a,b)
            for hits,hit_probability in upgrade_counts(rolls):
                counts=tuple(h+1 for h in hits)
                cr_count=counts[keys.index('critRate_')] if 'critRate_' in keys else 0
                spec=tuple(sorted((k,n,self.profile.weight(k)) for k,n in zip(keys,counts)
                                  if k!='critRate_' and self.profile.weight(k)>0))
                values,tail,moment=other_distribution(spec)
                mass=float(type_probability*hit_probability)
                for i,bound in enumerate(('lo','hi')):
                    thresholds,cr_probability=self.thresholds(slot,main,bound,cr_count)
                    probability,gain=tail_metrics(values,tail,moment,thresholds,minimum_gain)
                    total[i]+=mass*float(np.dot(cr_probability,probability))
                    total[2+i]+=mass*float(np.dot(cr_probability,gain))
                    if target is not None:
                        baseline=float(self.inventory.baseline.hi if bound=='lo' else self.inventory.baseline.lo)
                        pg,tg=target_metrics(values,tail,moment,thresholds,float(target)-baseline)
                        old=float(self.inventory.baseline.lo if bound=='lo' else self.inventory.baseline.hi)
                        total[4+i]+=mass*(1. if old>=target else float(np.dot(cr_probability,pg)))
                        total[6+i]+=mass*float(np.dot(cr_probability,tg))
        return total

    def evaluate(self,slot,main,selected,p_four=DEFINITION_FOUR_LINE_PROBABILITY,minimum_gain=0.):
        if slot not in COST or main not in self.profile.data['main_stats'][slot]:raise ValueError('Action outside profile')
        if not 0<=p_four<=1 or minimum_gain<0:raise ValueError('Invalid probability or gain threshold')
        three=self.conditional(slot,main,selected,4,minimum_gain)
        four=self.conditional(slot,main,selected,5,minimum_gain)
        mixed=(1-float(p_four))*three+float(p_four)*four
        def metrics(v):
            result={'probability_lower':float(v[0]),'probability_upper':float(v[1]),
                    'expected_gain_lower':float(v[2]),'expected_gain_upper':float(v[3])}
            if self.profile.data.get('resource_target_score') is not None:
                result.update(target_probability_lower=float(v[4]),target_probability_upper=float(v[5]),target_gain_lower=float(v[6]),target_gain_upper=float(v[7]))
            return result
        return {'slot':slot,'main':main,'selected':list(selected),'cost':COST[slot],
                **metrics(mixed),'expected_gain_per_elixir_lower':float(mixed[2]/COST[slot]),
                'p_four_assumed':float(p_four),'minimum_gain':minimum_gain,
                'conditional_three':metrics(three),'conditional_four':metrics(four)}

    def actions(self):
        for slot in SLOTS:
            for main in self.profile.data['main_stats'][slot]:
                # Include every legal pair, even zero-weight stats: no heuristic exclusion.
                for selected in combinations([k for k in ROLLS if k!=main],2):
                    yield slot,main,selected
