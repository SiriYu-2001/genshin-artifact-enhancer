"""Read-only fixed two-definition shortlist comparisons; not adaptive optimality."""
import hashlib
from itertools import combinations_with_replacement
import numpy as np
from .model import Artifact, SLOTS, ROLLS, MEANS
from .capped import CappedInventory, Point, pareto, main_crit
from .elixir import TYPE_WEIGHT


def sample_candidate(profile,action,n,seed=20260929):
    rng=np.random.default_rng(seed)
    all_keys=list(ROLLS);tiers=np.array([[float(v) for v in ROLLS[k]] for k in all_keys])
    selected=action['selected'];legal=[k for k in all_keys if k!=action['main'] and k not in selected]
    weights=np.array([TYPE_WEIGHT[k] for k in legal],dtype=float)
    first=rng.choice(len(legal),size=n,p=weights/weights.sum())
    second=np.empty(n,dtype=int)
    for i in range(len(legal)):
        mask=first==i;w=weights.copy();w[i]=0
        second[mask]=rng.choice(len(legal),size=mask.sum(),p=w/w.sum())
    lookup=np.array([all_keys.index(k) for k in legal])
    keys=np.column_stack((np.full(n,all_keys.index(selected[0])),np.full(n,all_keys.index(selected[1])),lookup[first],lookup[second]))
    values=tiers[keys,rng.integers(0,4,size=(n,4))]
    count=np.where(rng.random(n)<action['p_four_assumed'],5,4)
    hits=np.zeros(n,dtype=int);indices=np.arange(n)
    for step in range(5):
        active=count>step;force=(count-step<=2-hits)&active
        chosen=rng.integers(0,4,size=n);chosen[force]=rng.integers(0,2,size=force.sum())
        gain=tiers[keys[indices,chosen],rng.integers(0,4,size=n)]
        values[indices[active],chosen[active]]+=gain[active]
        hits+=(chosen<2)&active
    assert hits.min()>=2
    coef=np.array([float(profile.weight(k)/MEANS[k]) if k!='critRate_' else 0 for k in all_keys])
    cr=np.sum(values*(keys==all_keys.index('critRate_')),axis=1)
    other=np.sum(values*coef[keys],axis=1)
    return cr,other


def two_complements(inv,slots,bound):
    choices=[]
    for off in SLOTS:
        states=(Point(0,0),)
        for slot in SLOTS:
            if slot in slots:continue
            bucket=inv.buckets[bound,slot,slot!=off]
            states=pareto((Point(a.crit+b.crit,a.other+b.other,(),a.main_crit+b.main_crit)
                           for a in states for b in bucket),inv.cap)
        choices.extend(states)
    return pareto(choices,inv.cap)


def score(points,cr,other,main,inv):
    best=np.full(len(cr),-np.inf)
    for p in points:
        room=max(0,float(inv.cap-p.main_crit)-main)
        best=np.maximum(best,other+float(p.other)+float(inv.w)*np.minimum(cr+float(p.crit),room))
    return best


def compare(profile,pool,actions,n=200000,budget=None,minimum_gain=0.):
    inv=CappedInventory(pool,profile);samples={};singles={};main={}
    for i,a in enumerate(actions):
        artifact=Artifact('proposal',profile.data['set_key'],a['slot'],a['main'],20,())
        main[i]=float(main_crit(artifact))
        for draw in (0,1):
            seed=int.from_bytes(hashlib.sha256(f'{profile.data["character"]}:{i}:{draw}:20260929'.encode()).digest()[:8],'little')
            cr,other=sample_candidate(profile,a,n,seed);samples[i,draw]=(cr,other)
            for bound in ('lo','hi'):singles[i,draw,bound]=score(inv.frontier(artifact,bound),cr,other,main[i],inv)
    results=[]
    for i,j in combinations_with_replacement(range(len(actions)),2):
        a,b=actions[i],actions[j]
        if budget is not None and a['cost']+b['cost']>budget:continue
        record={'slots':[a['slot'],b['slot']],'cost':a['cost']+b['cost'],'definitions':2,'actions':[a,b],'samples':n,'metrics':{}}
        for bound in ('lo','hi'):
            baseline=float(inv.baseline.hi if bound=='lo' else inv.baseline.lo)
            forced=np.maximum(singles[i,0,bound],singles[j,1,bound])
            if a['slot']!=b['slot']:
                cr=samples[i,0][0]+samples[j,1][0];other=samples[i,0][1]+samples[j,1][1]
                both=score(two_complements(inv,{a['slot'],b['slot']},bound),cr,other,main[i]+main[j],inv)
                forced=np.maximum(forced,both)
            value=np.maximum(baseline,forced)
            gain=np.maximum(value-baseline,0);p=float(np.mean(gain>minimum_gain+1e-10))
            record['metrics'][bound]={'probability':p,'expected_gain':float(gain.mean()),
                'probability_95_halfwidth':1.96*float(np.sqrt(p*(1-p)/n)),
                'expected_gain_95_halfwidth':1.96*float(gain.std(ddof=1)/np.sqrt(n))}
            target=profile.data.get('resource_target_score')
            if target is not None:
                old=float(inv.baseline.lo if bound=='lo' else inv.baseline.hi)
                tg=np.maximum(np.minimum(value,target)-min(baseline,target),0)
                record['metrics'][bound].update(target_probability=float(np.mean(np.maximum(forced,old)>=target-1e-10)),target_gain=float(tg.mean()))
        results.append(record)
    return results

