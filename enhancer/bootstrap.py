"""Build the first feasible +20 set using a reproducible expected-score search.

This is a sampled, bounded search, explicitly not a proof of the global maximum
of the true expectation. It scores whole outcomes (including CR saturation and
ER feasibility), never the score of mean attributes. Ordinary sequential
replacement probabilities remain exact and unchanged once a baseline exists.
"""
import hashlib
import json
import time
from dataclasses import replace
from fractions import Fraction as F
import numpy as np

from .model import SLOTS,ROLLS,MEANS,UncertainObservation
from .capped import components,energy_bounds,main_crit


def terminal_samples(a,profile,n=1024):
    cr,other=components(a,profile)
    er=energy_bounds(a,terminal=True)
    values=np.tile([float(cr.lo),float(other.lo),float(er.lo)],(n,1))
    identity=[a.set_key,a.slot,a.main,a.level,[(s.key,str(s.value),s.pending) for s in a.stats]]
    seed=int.from_bytes(hashlib.sha256(('bootstrap-v1:'+json.dumps(identity,sort_keys=True)).encode()).digest()[:8],'little')
    rng=np.random.default_rng(seed);keys=[s.key for s in a.stats]
    for _ in range(a.random_rolls_left):
        hits=rng.integers(0,4,n);tiers=rng.integers(0,4,n)
        for index,key in enumerate(keys):
            mask=hits==index;gain=np.array([float(v) for v in ROLLS[key]])[tiers[mask]]
            if key=='critRate_':values[mask,0]+=gain
            else:values[mask,1]+=gain*float(profile.weight(key)/MEANS[key])
            if key=='enerRech_':values[mask,2]+=gain
    return values


def plan(pool,profile,names=None,*,samples=1024,max_nodes=15000,time_limit=6,committed=None):
    if committed:
        lookup={a.id:a for a in pool};chosen=[lookup.get(k) for k in committed['items']]
        if (len(chosen)==5 and all(a is not None and profile.allows(a) and not(a.special=='defined' and a.level<20) for a in chosen)
                and {a.slot for a in chosen}==set(SLOTS) and any(a.level<20 for a in chosen)
                and sum(a.set_key==profile.data['set_key'] for a in chosen)>=4):
            return dict(committed,pending_items=[a.id for a in chosen if a.level<20],committed=True)
    candidates=[];draws={};deferred=[]
    for a in pool:
        if not profile.allows(a):continue
        if a.level<20 and a.special=='defined':
            deferred.append(a.id);continue
        try:draws[a.id]=terminal_samples(replace(a,special='ordinary') if a.special=='unknown' else a,profile,samples)
        except (UncertainObservation,ValueError):deferred.append(a.id);continue
        candidates.append(a)
    if not any(a.level<20 for a in candidates):return None
    configured_cap=profile.data.get('artifact_crit_rate_cap')
    weight=float(profile.weight('critRate_')/MEANS['critRate_']);cap=float(configured_cap if configured_cap is not None else 100000);floor=float(profile.data.get('artifact_energy_recharge_min',0))
    linear={a.id:draws[a.id][:,1]+weight*draws[a.id][:,0] for a in candidates}
    best=None;best_value=-1.;nodes=0;limited=False;start=time.monotonic()
    def evaluate(rows,main):
        feasible=rows[:,2]>=floor-1e-10
        scores=rows[:,1]+weight*np.minimum(rows[:,0],max(0,cap-main))
        return np.where(feasible,scores,0.),feasible
    # Largest bucket last makes upper bounds prune earlier. All 4+1 layouts
    # remain eligible, including a non-set item in any one slot.
    for off in SLOTS:
        buckets={s:[a for a in candidates if a.slot==s and (s==off or a.set_key==profile.data['set_key'])] for s in SLOTS}
        if any(not b for b in buckets.values()):continue
        slots=sorted(SLOTS,key=lambda s:len(buckets[s]))
        for b in buckets.values():b.sort(key=lambda a:float(linear[a.id].mean()),reverse=True)
        max_linear=[np.max([linear[a.id] for a in buckets[s]],axis=0) for s in slots]
        max_energy=[np.max([draws[a.id][:,2] for a in buckets[s]],axis=0) for s in slots]
        def visit(i,rows,main,chosen):
            nonlocal best,best_value,nodes,limited
            nodes+=1
            if nodes>max_nodes or time.monotonic()-start>time_limit:limited=True;return
            if i==5:
                if all(a.level==20 for a in chosen):return
                scores,feasible=evaluate(rows,main);value=float(scores.mean())
                if feasible.any() and value>best_value:best_value=value;best=(tuple(chosen),scores,feasible)
                return
            upper=rows[:,1]+weight*np.minimum(rows[:,0],max(0,cap-main))+sum(max_linear[i:])
            reachable=rows[:,2]+sum(max_energy[i:])>=floor-1e-10
            if float(np.where(reachable,upper,0).mean())<=best_value:return
            for a in buckets[slots[i]]:
                visit(i+1,rows+draws[a.id],main+float(main_crit(a,terminal=True)),chosen+[a])
                if limited:return
        visit(0,np.zeros((samples,3)),0.,[])
        if limited:break
    if best is None:return None
    chosen,scores,feasible=best
    return {'method':'bounded_sampled_expected_build_v1','samples':samples,'searched_nodes':nodes,'search_limited':limited,
            'global_optimality':'not_claimed' if limited else 'sample_objective_only',
            'expected_score_lower_model':float(scores.mean()),'mc95_halfwidth':float(1.96*scores.std(ddof=1)/np.sqrt(samples)),
            'feasibility_probability_estimate':float(feasible.mean()),'items':[a.id for a in chosen],
            'pending_items':[a.id for a in chosen if a.level<20],'deferred_count':len(deferred),
            'scope':'按当前观测的保守属性边界模拟升满级；对整套计入暴击封顶，未达到额外充能下限的结果记0分。搜索选择已考察组合中期望分最高者；不保证真实期望全局最优。'}


def decisions(pool,profile,names,committed=None):
    proposal=plan(pool,profile,names,committed=committed)
    if not proposal:return [],None
    lookup={a.id:a for a in pool}
    return [dict(id=identifier,name=names[identifier],level=lookup[identifier].level,set_key=lookup[identifier].set_key,
                 action='enhance',reason='bootstrap_mature_build',bootstrap=True,probability_lower=None,
                 probability_upper=None,next_checkpoint=min(20,(lookup[identifier].level//4+1)*4))
            for identifier in proposal['pending_items']],proposal
