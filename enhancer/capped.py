"""4+1 optimization and joint roll probabilities with a whole-build CR score cap."""
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction as F
from functools import lru_cache
from math import lcm
import json
from pathlib import Path

from .model import Bounds, MEANS, ROLLS, SLOTS, UncertainObservation, possible_values


@dataclass(frozen=True)
class Point:
    crit: F
    other: F
    ids: tuple[str, ...] = ()
    main_crit: F = F(0)
    energy: F = F(0)


_MAIN = json.loads((Path(__file__).resolve().parents[1] / "data/artifact-main-go.json").read_text(encoding="utf-8-sig"))


def main_crit(artifact, terminal=False):
    if artifact.main != "critRate_":
        return F(0)
    return F(str(_MAIN[str(artifact.rarity)]["critRate_"][20 if terminal else artifact.level])) * 100


def main_energy(artifact, terminal=False):
    if artifact.main!='enerRech_':return F(0)
    return F(str(_MAIN[str(artifact.rarity)]['enerRech_'][20 if terminal else artifact.level]))*100


def energy_bounds(artifact,terminal=False):
    main=main_energy(artifact,terminal);result=Bounds(main,main)
    for stat in artifact.stats:
        if stat.key=='enerRech_':
            values=Bounds(stat.value,stat.value) if stat.exact else possible_values(stat.key,stat.value,1 if stat.pending else 1+artifact.level//4)
            result+=values
    return result


def components(artifact, profile, *, include_pending=True):
    artifact.validate()
    crit, other = Bounds(F(0), F(0)), Bounds(F(0), F(0))
    for stat in artifact.stats:
        values = (Bounds(stat.value, stat.value) if stat.exact else
                  possible_values(stat.key, stat.value, 1 if stat.pending else 1 + artifact.level // 4))
        if stat.pending and not include_pending:
            continue
        if stat.key == "critRate_":
            crit = values
        else:
            w = profile.weight(stat.key) / MEANS[stat.key]
            other += Bounds(values.lo * w, values.hi * w)
    return crit, other


def pareto(points, cap, energy_floor=F(0)):
    if energy_floor>0:
        # Three-dimensional skyline: score, capped CR, and required ER. Never
        # discard a lower-scoring item whose ER can make the whole set feasible.
        groups=defaultdict(dict)
        for p in points:
            p=Point(min(p.crit,max(F(0),cap-p.main_crit)),p.other,p.ids,p.main_crit,min(p.energy,energy_floor))
            key=p.crit,p.energy;old=groups[p.main_crit].get(key)
            if old is None or p.other>old.other:groups[p.main_crit][key]=p
        result=[]
        for group in groups.values():
            energies=sorted({p.energy for p in group.values()},reverse=True);ranks={v:i+1 for i,v in enumerate(energies)}
            tree=[None]*(len(energies)+1)
            for p in sorted(group.values(),key=lambda p:(p.crit,p.energy,p.other),reverse=True):
                idx=ranks[p.energy];best=None;j=idx
                while j:
                    if tree[j] is not None:best=tree[j] if best is None else max(best,tree[j])
                    j-=j&-j
                if best is not None and best>=p.other:continue
                result.append(p)
                while idx<len(tree):
                    tree[idx]=p.other if tree[idx] is None else max(tree[idx],p.other);idx+=idx&-idx
        return tuple(result)
    by_main = defaultdict(dict)
    for p in points:
        p = Point(min(p.crit, max(F(0), cap - p.main_crit)), p.other, p.ids, p.main_crit)
        best_by_crit = by_main[p.main_crit]
        previous = best_by_crit.get(p.crit)
        if previous is None or p.other > previous.other:
            best_by_crit[p.crit] = p
    result = []
    for best_by_crit in by_main.values():
        best_other = None
        for cr in sorted(best_by_crit, reverse=True):
            point = best_by_crit[cr]
            if best_other is None or point.other > best_other:
                result.append(point)
                best_other = point.other
    return tuple(result)


class FixedMainEnvelope:
    """Query best complement in O(log n), accounting for the candidate's CR."""
    def __init__(self, points, cap, weight):
        self.points = tuple(sorted(points, key=lambda p: p.crit))
        self.crits = tuple(p.crit for p in self.points)
        self.cap, self.weight = cap, weight
        self.prefix, self.suffix = [], [F(0)] * len(self.points)
        value = None
        for p in self.points:
            value = max(value, p.other + weight * p.crit) if value is not None else p.other + weight * p.crit
            self.prefix.append(value)
        value = None
        for i in range(len(self.points) - 1, -1, -1):
            value = max(value, self.points[i].other) if value is not None else self.points[i].other
            self.suffix[i] = value

    @lru_cache(maxsize=4096)
    def query(self, candidate_cr):
        if not self.points:
            return None
        boundary = bisect_right(self.crits, self.cap - candidate_cr)
        possibilities = []
        if boundary:
            possibilities.append(self.prefix[boundary - 1] + self.weight * candidate_cr)
        if boundary < len(self.points):
            possibilities.append(self.suffix[boundary] + self.weight * self.cap)
        return max(possibilities)


class Envelope:
    def __init__(self, points, cap, weight, energy_floor=F(0)):
        self.points, self.cap, self.weight = points, cap, weight
        self.energy_floor=energy_floor
        self.groups = defaultdict(list)
        self.queries = {}
        for p in points:
            self.groups[p.main_crit].append(p)

    def query(self, candidate_cr, candidate_main=F(0), candidate_energy=F(0)):
        values = []
        for existing_main, points in self.groups.items():
            required=max(F(0),self.energy_floor-candidate_energy)
            key = existing_main, candidate_main, required
            if key not in self.queries:
                self.queries[key] = FixedMainEnvelope([p for p in points if p.energy>=required], max(F(0), self.cap - existing_main - candidate_main), self.weight)
            value=self.queries[key].query(candidate_cr)
            if value is not None:values.append(value)
        return max(values) if values else None


class CappedInventory:
    def __init__(self, pool, profile):
        self.profile = profile
        configured_cap=profile.data.get('artifact_crit_rate_cap')
        self.cap = F(str(configured_cap if configured_cap is not None else 100000))
        if self.cap < 0:
            raise ValueError("Negative CR cap")
        self.w = profile.weight("critRate_") / MEANS["critRate_"]
        self.energy_floor=F(str(profile.data.get('artifact_energy_recharge_min',0)))
        if not 0<=self.energy_floor<=300:raise ValueError('圣遗物额外充能下限必须在0–300之间')
        self.buckets = {}
        mature = [a for a in pool if a.level == 20 and profile.allows(a)]
        data = {a.id: components(a, profile) for a in mature}
        for bound in ("lo", "hi"):
            for slot in SLOTS:
                for target_only in (True, False):
                    self.buckets[bound, slot, target_only] = pareto(
                        (Point(getattr(data[a.id][0], bound), getattr(data[a.id][1], bound), (a.id,), main_crit(a),getattr(energy_bounds(a),bound) if self.energy_floor else F(0))
                         for a in mature if a.slot == slot and (not target_only or a.set_key == profile.data["set_key"])), self.cap,self.energy_floor)
        self.cache = {}
        self.full_lo = tuple(p for p in self.frontier(None, "lo") if p.energy>=self.energy_floor)
        self.full_hi = tuple(p for p in self.frontier(None, "hi") if p.energy>=self.energy_floor)
        self.baseline = (Bounds(max(self.score(p) for p in self.full_lo), max(self.score(p) for p in self.full_hi))
                         if self.full_lo and self.full_hi else None)
        self.best_ids = max(self.full_lo, key=self.score).ids if self.full_lo else ()

    def score(self, point):
        return point.other + self.w * min(max(F(0), self.cap - point.main_crit), point.crit)

    def frontier(self, candidate, bound):
        key = (None if candidate is None else candidate.slot,
               None if candidate is None else candidate.set_key == self.profile.data["set_key"], bound)
        if key in self.cache:
            return self.cache[key]
        choices = []
        for off in SLOTS:
            if candidate and candidate.slot != off and candidate.set_key != self.profile.data["set_key"]:
                continue
            states = (Point(F(0), F(0)),)
            for slot in SLOTS:
                if candidate and slot == candidate.slot:
                    continue
                bucket = self.buckets[bound, slot, slot != off]
                states = pareto((Point(a.crit + b.crit, a.other + b.other, a.ids + b.ids, a.main_crit + b.main_crit,a.energy+b.energy)
                                 for a in states for b in bucket), self.cap,self.energy_floor)
                if not states:
                    break
            choices.extend(states)
        result = pareto(choices, self.cap,self.energy_floor)
        self.cache[key] = result
        return result

    def complement(self, candidate, bound):
        return Envelope(self.frontier(candidate, bound), self.cap, self.w,self.energy_floor)


@lru_cache(maxsize=256)
def energy_joint_distribution(keys,weights,rolls):
    steps=Counter((v if k=='critRate_' else F(0),v if k=='enerRech_' else F(0),F(0) if k=='critRate_' else w*v/MEANS[k])
                  for k,w in zip(keys,weights) for v in ROLLS[k])
    groups=defaultdict(list)
    for (cr,er,score),n in exact_roll_counts(tuple(sorted(steps.items())),rolls):groups[cr,er].append((score,n))
    result=[]
    for (cr,er),values in groups.items():
        values.sort();cumulative=[0]
        for _,n in values:cumulative.append(cumulative[-1]+n)
        result.append((cr,er,tuple(s for s,n in values),tuple(cumulative)))
    return tuple(result),16**rolls


@lru_cache(maxsize=256)
def joint_distribution(keys, weights, rolls):
    steps = Counter((value, F(0)) if key == "critRate_" else (F(0), weight * value / MEANS[key])
                    for key, weight in zip(keys, weights) for value in ROLLS[key])
    groups = defaultdict(list)
    for (crit, other), count in exact_roll_counts(tuple(sorted(steps.items())),rolls):
        groups[crit].append((other, count))
    result = []
    for crit, values in groups.items():
        values.sort()
        scores, cumulative, total = [], [0], 0
        for score, count in values:
            scores.append(score)
            total += count
            cumulative.append(total)
        result.append((crit, tuple(scores), tuple(cumulative)))
    return tuple(result), 16 ** rolls


@lru_cache(maxsize=256)
def exact_roll_counts(steps,rolls):
    """Exact rational lattice convolution; permutations share one cache entry.

    Multiplying each coordinate by its denominator LCM is lossless. Inner loops
    use integers; only the final support is converted back to Fraction values.
    """
    dimensions=len(steps[0][0])
    scales=tuple(lcm(*(F(point[d]).denominator for point,_ in steps)) for d in range(dimensions))
    integer_steps=tuple((tuple(int(v*scale) for v,scale in zip(point,scales)),n) for point,n in steps)
    states={(0,)*dimensions:1}
    for _ in range(rolls):
        nxt=defaultdict(int)
        if dimensions==2:
            for (x,y),n in states.items():
                for (dx,dy),m in integer_steps:nxt[x+dx,y+dy]+=n*m
        else:
            for (x,y,z),n in states.items():
                for (dx,dy,dz),m in integer_steps:nxt[x+dx,y+dy,z+dz]+=n*m
        states=nxt
    return tuple((tuple(F(v,scale) for v,scale in zip(point,scales)),n) for point,n in states.items())


def evaluate_capped(candidate, pool, profile, prepared=None):
    if not profile.allows(candidate):
        return {"id": candidate.id, "action": "skip", "reason": "profile_or_reservation"}
    try:
        crit, other = components(candidate, profile)
        rolls = candidate.random_rolls_left
        prepared = prepared or CappedInventory(pool, profile)
        low, high = prepared.complement(candidate, "lo"), prepared.complement(candidate, "hi")
    except UncertainObservation as exc:
        return {"id": candidate.id, "action": "reread", "reason": str(exc)}
    if prepared.baseline is None or not low.points or not high.points:
        return {"id": candidate.id, "action": "stop", "reason": "no_feasible_mature_build"}
    keys = tuple(sorted(s.key for s in candidate.stats))
    # Every complement's CR response has slope at most profile CR weight / mean.
    # Thus its upper envelope is also Lipschitz with that bound. Ignoring cap
    # saturation and granting the best weighted hit at EVERY remaining roll
    # is optimistic even when the optimal complement changes with the outcome.
    best_hit=max(profile.weight(k)*max(ROLLS[k])/MEANS[k] for k in keys)
    er=energy_bounds(candidate,terminal=True) if prepared.energy_floor else Bounds(F(0),F(0))
    max_er=rolls*max(ROLLS['enerRech_']) if 'enerRech_' in keys else F(0)
    optimistic=high.query(crit.hi,main_crit(candidate,terminal=True),er.hi+max_er)
    pruned=optimistic is None or other.hi+optimistic+rolls*best_hit<=prepared.baseline.lo
    if pruned:
        p_lo=p_hi=F(0)
    else:
        if prepared.energy_floor:groups,denominator=energy_joint_distribution(keys,tuple(profile.weight(k) for k in keys),rolls)
        else:
            old_groups,denominator=joint_distribution(keys,tuple(profile.weight(k) for k in keys),rolls)
            groups=((cr,F(0),gains,cumulative) for cr,gains,cumulative in old_groups)
        low_count = high_count = 0
        for gain_cr,gain_er,gains,cumulative in groups:
            lo=low.query(crit.lo+gain_cr,main_crit(candidate,terminal=True),er.lo+gain_er)
            hi=high.query(crit.hi+gain_cr,main_crit(candidate,terminal=True),er.hi+gain_er)
            if lo is not None:low_count+=cumulative[-1]-cumulative[bisect_right(gains,prepared.baseline.hi-other.lo-lo)]
            if hi is not None:high_count+=cumulative[-1]-cumulative[bisect_right(gains,prepared.baseline.lo-other.hi-hi)]
        p_lo, p_hi = F(low_count, denominator), F(high_count, denominator)
    action = ("complete" if candidate.level == 20 else "enhance" if p_lo >= profile.threshold
              else "retain" if p_hi < profile.threshold else "reread")
    return {"id": candidate.id, "level": candidate.level, "action": action,
            "next_checkpoint": min(20, (candidate.level // 4 + 1) * 4), "random_rolls_left": rolls,
            "probability_lower": float(p_lo), "probability_upper": float(p_hi),
            "probability_fraction_lower": str(p_lo), "artifact_crit_rate_cap": float(prepared.cap),
            "baseline": [float(prepared.baseline.lo), float(prepared.baseline.hi)],
            "candidate_crit_rate": [float(crit.lo), float(crit.hi)],
            "candidate_other_score": [float(other.lo), float(other.hi)],"pruned_by_upper_bound":pruned}


def build_score(build, profile, displayed=False):
    configured_cap=profile.data.get('artifact_crit_rate_cap')
    cap = F(str(configured_cap if configured_cap is not None else 100000))
    main = sum(main_crit(a) for a in build)
    available = max(F(0), cap - main)
    w = profile.weight("critRate_") / MEANS["critRate_"]
    if displayed:
        cr = sum(s.value for a in build for s in a.stats if s.key == "critRate_" and not s.pending)
        other = sum(s.value * profile.weight(s.key) / MEANS[s.key] for a in build for s in a.stats
                    if s.key != "critRate_" and not s.pending)
        return other + w * min(cr, available)
    parts = [components(a, profile, include_pending=False) for a in build]
    crit = sum((c for c, _ in parts), Bounds(F(0), F(0)))
    other = sum((o for _, o in parts), Bounds(F(0), F(0)))
    return Bounds(other.lo + w * min(crit.lo, available), other.hi + w * min(crit.hi, available))
