"""4+1 optimization and joint roll probabilities with a whole-build CR score cap."""
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction as F
from functools import lru_cache
import json
from pathlib import Path

from .model import Bounds, MEANS, ROLLS, SLOTS, UncertainObservation, possible_values


@dataclass(frozen=True)
class Point:
    crit: F
    other: F
    ids: tuple[str, ...] = ()
    main_crit: F = F(0)


_MAIN = json.loads((Path(__file__).resolve().parents[1] / "data/artifact-main-go.json").read_text(encoding="utf-8-sig"))


def main_crit(artifact, terminal=False):
    if artifact.main != "critRate_":
        return F(0)
    return F(str(_MAIN[str(artifact.rarity)]["critRate_"][20 if terminal else artifact.level])) * 100


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


def pareto(points, cap):
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
    def __init__(self, points, cap, weight):
        self.points, self.cap, self.weight = points, cap, weight
        self.groups = defaultdict(list)
        self.queries = {}
        for p in points:
            self.groups[p.main_crit].append(p)

    def query(self, candidate_cr, candidate_main=F(0)):
        values = []
        for existing_main, points in self.groups.items():
            key = existing_main, candidate_main
            if key not in self.queries:
                self.queries[key] = FixedMainEnvelope(points, max(F(0), self.cap - existing_main - candidate_main), self.weight)
            values.append(self.queries[key].query(candidate_cr))
        return max(values) if values else None


class CappedInventory:
    def __init__(self, pool, profile):
        self.profile = profile
        self.cap = F(str(profile.data["artifact_crit_rate_cap"]))
        if self.cap < 0:
            raise ValueError("Negative CR cap")
        self.w = profile.weight("critRate_") / MEANS["critRate_"]
        self.buckets = {}
        mature = [a for a in pool if a.level == 20 and profile.allows(a)]
        data = {a.id: components(a, profile) for a in mature}
        for bound in ("lo", "hi"):
            for slot in SLOTS:
                for target_only in (True, False):
                    self.buckets[bound, slot, target_only] = pareto(
                        (Point(getattr(data[a.id][0], bound), getattr(data[a.id][1], bound), (a.id,), main_crit(a))
                         for a in mature if a.slot == slot and (not target_only or a.set_key == profile.data["set_key"])), self.cap)
        self.cache = {}
        self.full_lo = self.frontier(None, "lo")
        self.full_hi = self.frontier(None, "hi")
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
                states = pareto((Point(a.crit + b.crit, a.other + b.other, a.ids + b.ids, a.main_crit + b.main_crit)
                                 for a in states for b in bucket), self.cap)
                if not states:
                    break
            choices.extend(states)
        result = pareto(choices, self.cap)
        self.cache[key] = result
        return result

    def complement(self, candidate, bound):
        return Envelope(self.frontier(candidate, bound), self.cap, self.w)


@lru_cache(maxsize=256)
def joint_distribution(keys, weights, rolls):
    steps = Counter((value, F(0)) if key == "critRate_" else (F(0), weight * value / MEANS[key])
                    for key, weight in zip(keys, weights) for value in ROLLS[key])
    states = Counter({(F(0), F(0)): 1})
    for _ in range(rolls):
        nxt = Counter()
        for (crit, other), count in states.items():
            for (dc, ds), multiplicity in steps.items():
                nxt[crit + dc, other + ds] += count * multiplicity
        states = nxt
    groups = defaultdict(list)
    for (crit, other), count in states.items():
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
    upper=other.hi+high.query(crit.hi,main_crit(candidate,terminal=True))+rolls*best_hit
    pruned=upper<=prepared.baseline.lo
    if pruned:
        p_lo=p_hi=F(0)
    else:
        groups, denominator = joint_distribution(keys, tuple(profile.weight(k) for k in keys), rolls)
        low_count = high_count = 0
        for gain_cr, gains, cumulative in groups:
            threshold_lo = prepared.baseline.hi - other.lo - low.query(crit.lo + gain_cr, main_crit(candidate, terminal=True))
            threshold_hi = prepared.baseline.lo - other.hi - high.query(crit.hi + gain_cr, main_crit(candidate, terminal=True))
            low_count += cumulative[-1] - cumulative[bisect_right(gains, threshold_lo)]
            high_count += cumulative[-1] - cumulative[bisect_right(gains, threshold_hi)]
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
    cap = F(str(profile.data["artifact_crit_rate_cap"]))
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
