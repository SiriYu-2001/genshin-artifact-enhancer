from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from fractions import Fraction as F
from functools import lru_cache
from itertools import product
from pathlib import Path
import json

SLOTS = ("flower", "plume", "sands", "goblet", "circlet")
# UI units: percentages are percentage points, not fractions.
ROLLS = {
    "hp": (209.13, 239, 268.88, 298.75),
    "atk": (13.62, 15.56, 17.51, 19.45),
    "def": (16.20, 18.52, 20.83, 23.15),
    "hp_": (4.08, 4.66, 5.25, 5.83),
    "atk_": (4.08, 4.66, 5.25, 5.83),
    "def_": (5.10, 5.83, 6.56, 7.29),
    "eleMas": (16.32, 18.65, 20.98, 23.31),
    "enerRech_": (4.53, 5.18, 5.83, 6.48),
    "critRate_": (2.72, 3.11, 3.50, 3.89),
    "critDMG_": (5.44, 6.22, 6.99, 7.77),
}
ROLLS = {k: tuple(F(str(v)) for v in values) for k, values in ROLLS.items()}
MEANS = {k: sum(v) / 4 for k, v in ROLLS.items()}


class UncertainObservation(ValueError):
    pass


@dataclass(frozen=True)
class Bounds:
    lo: F
    hi: F

    def __add__(self, other):
        if other == 0:
            return self
        return Bounds(self.lo + other.lo, self.hi + other.hi)

    __radd__ = __add__


@dataclass(frozen=True)
class Stat:
    key: str
    value: F
    pending: bool = False
    exact: bool = False

    @classmethod
    def from_dict(cls, data):
        value=data["value"]
        if not data.get("exact",False):
            # yas may serialize displayed 3.5% as 3.5000000000000004.
            # Remove binary conversion noise at the game's display precision.
            value=round(float(value),1 if data["key"].endswith("_") else 0)
        return cls(data["key"], F(str(value)), data.get("pending", False),
                   data.get("exact", False))


@lru_cache(maxsize=512)
def possible_values(key, displayed, max_hits):
    """A conservative union over possible roll histories, never a fabricated exact value.

    The small tolerance admits float32 display boundary differences. Total-roll
    constraints between stats are not imposed; bounds may therefore be wider.
    """
    half_unit = F(1, 20) if key.endswith("_") else F(1, 2)
    values, totals = set(), {F(0)}
    for _ in range(max_hits):
        totals = {a + b for a in totals for b in ROLLS[key]}
        values.update(v for v in totals if abs(v - displayed) <= half_unit + F(1, 10000))
    if not values:
        raise UncertainObservation(f"{key}={displayed}: no legal roll sum")
    return Bounds(min(values), max(values))


@dataclass(frozen=True)
class Artifact:
    id: str
    set_key: str
    slot: str
    main: str
    level: int
    stats: tuple[Stat, ...]
    rarity: int = 5
    equipped: str = ""
    locked: bool = False
    special: str = "ordinary"  # unknown/defined pieces require separate state data
    excluded: bool = False  # explicit omission in an imported inventory

    def validate(self):
        if self.rarity != 5 or not 0 <= self.level <= 20 or self.slot not in SLOTS:
            raise UncertainObservation("unsupported rarity, level or slot")
        if len(self.stats) != 4 or len({s.key for s in self.stats}) != 4:
            raise UncertainObservation("four known stat types (including preview) required")
        if any(s.key not in ROLLS or s.value <= 0 or s.key == self.main for s in self.stats):
            raise UncertainObservation("invalid substats")
        pending = sum(s.pending for s in self.stats)
        if pending > 1 or (self.level >= 4 and pending):
            raise UncertainObservation("pending stat inconsistent with observed level")
        if self.special != "ordinary" and self.level < 20:
            raise UncertainObservation("defined/unknown artifact guarantee state unsupported")

    @property
    def random_rolls_left(self):
        self.validate()
        # The known fourth stat activates deterministically at +4.
        return 5 - self.level // 4 - int(any(s.pending for s in self.stats))


@dataclass
class Profile:
    data: dict

    @classmethod
    def load(cls, path):
        obj = cls(json.loads(Path(path).read_text(encoding="utf-8-sig")))
        if obj.data["normalization"] != "mean_roll" or obj.data["set_requirement"] != 4:
            raise ValueError("this planner supports mean-roll scoring and 4+1")
        if not 0 <= obj.threshold <= 1 or any(F(str(v)) < 0 for v in obj.data["weights"].values()):
            raise ValueError("invalid threshold/weights")
        if not 0<=F(str(obj.data.get('artifact_energy_recharge_min',0)))<=300:raise ValueError('invalid artifact energy recharge minimum')
        return obj

    @property
    def threshold(self):
        return F(str(self.data["threshold"]))

    def weight(self, key):
        return F(str(self.data["weights"].get(key, 0)))

    def allows(self, artifact):
        return (artifact.rarity == 5 and not artifact.excluded
                and artifact.main in self.data["main_stats"].get(artifact.slot, [])
                and artifact.id not in self.data["reserved_ids"]
                and (not artifact.equipped or "*" in self.data["allowed_equipped_characters"]
                     or artifact.equipped in self.data["allowed_equipped_characters"]))

    def score(self, artifact, include_pending=True):
        artifact.validate()
        total = Bounds(F(0), F(0))
        for stat in artifact.stats:
            # Validate zero-weight observations too: OCR errors must not disappear.
            vals = (Bounds(stat.value, stat.value) if stat.exact else
                    possible_values(stat.key, stat.value, 1 if stat.pending else 1 + artifact.level // 4))
            if stat.pending and not include_pending:
                continue
            coefficient = self.weight(stat.key) / MEANS[stat.key]
            total += Bounds(vals.lo * coefficient, vals.hi * coefficient)
        return total


@lru_cache(maxsize=256)
def gain_distribution(keys, weights, rolls):
    """Exact rational scores and integer outcome counts; no Monte Carlo sampling."""
    steps = Counter(weight * value / MEANS[key]
                    for key, weight in zip(keys, weights) for value in ROLLS[key])
    dist = Counter({F(0): 1})
    for _ in range(rolls):
        nxt = Counter()
        for score, count in dist.items():
            for gain, multiplicity in steps.items():
                nxt[score + gain] += count * multiplicity
        dist = nxt
    return tuple(dist.items()), 16 ** rolls


def best_build(pool, profile, forced=None):
    """Return score bounds, maximizing all five choices of off-set slot.

    With forced set, return only the four-item complement. The forced artifact
    does not have to be +20. Duplicate physical pieces retain distinct IDs.
    """
    if profile.data.get('artifact_energy_recharge_min',0):
        from .capped import CappedInventory
        if forced is not None:raise ValueError('ER-constrained complements require a candidate-dependent CappedInventory envelope')
        return CappedInventory(pool,profile).baseline
    pool = [a for a in pool if a.level == 20 and profile.allows(a)
            and (forced is None or a.id != forced.id)]
    options = []
    for off_slot in SLOTS:
        if forced and forced.slot != off_slot and forced.set_key != profile.data["set_key"]:
            continue
        total = Bounds(F(0), F(0))
        for slot in SLOTS:
            if forced and slot == forced.slot:
                continue
            matches = [profile.score(a) for a in pool if a.slot == slot and
                       (slot == off_slot or a.set_key == profile.data["set_key"])]
            if not matches:
                break
            total += Bounds(max(s.lo for s in matches), max(s.hi for s in matches))
        else:
            options.append(total)
    if not options:
        return None
    return Bounds(max(s.lo for s in options), max(s.hi for s in options))


class InventoryEvaluation:
    """One immutable +20 baseline per scan/update; reuse its ten slot maxima."""
    def __init__(self, pool, profile):
        self.profile = profile
        self.maxima = {}
        for slot in SLOTS:
            for target_only in (True, False):
                scores = [profile.score(a) for a in pool if a.level == 20 and profile.allows(a)
                          and a.slot == slot and (not target_only or a.set_key == profile.data["set_key"])]
                self.maxima[slot, target_only] = (Bounds(max(s.lo for s in scores), max(s.hi for s in scores))
                                                   if scores else None)
        self.baseline = self.complement(None)

    def complement(self, candidate):
        choices = []
        for off in SLOTS:
            if candidate and candidate.slot != off and candidate.set_key != self.profile.data["set_key"]:
                continue
            pieces = [self.maxima[slot, slot != off] for slot in SLOTS if not candidate or slot != candidate.slot]
            if all(p is not None for p in pieces):
                choices.append(sum(pieces, Bounds(F(0), F(0))))
        return Bounds(max(c.lo for c in choices), max(c.hi for c in choices)) if choices else None


def evaluate(candidate, pool, profile, prepared=None):
    if profile.data.get("artifact_crit_rate_cap") is not None or profile.data.get('artifact_energy_recharge_min',0):
        from .capped import evaluate_capped
        return evaluate_capped(candidate, pool, profile, prepared)
    if not profile.allows(candidate):
        return {"id": candidate.id, "action": "skip", "reason": "profile_or_reservation"}
    try:
        current = profile.score(candidate)
        remaining = candidate.random_rolls_left
        baseline = prepared.baseline if prepared else best_build(pool, profile)
        complement = prepared.complement(candidate) if prepared else best_build(pool, profile, candidate)
    except UncertainObservation as exc:
        return {"id": candidate.id, "action": "reread", "reason": str(exc)}
    if baseline is None or complement is None:
        return {"id": candidate.id, "action": "stop", "reason": "no_feasible_mature_build"}
    keys = tuple(sorted(s.key for s in candidate.stats))
    dist, denominator = gain_distribution(keys, tuple(profile.weight(k) for k in keys), remaining)
    low_count = sum(n for gain, n in dist if current.lo + gain + complement.lo > baseline.hi)
    high_count = sum(n for gain, n in dist if current.hi + gain + complement.hi > baseline.lo)
    p_lo, p_hi = F(low_count, denominator), F(high_count, denominator)
    action = ("complete" if candidate.level == 20 else "enhance" if p_lo >= profile.threshold
              else "retain" if p_hi < profile.threshold else "reread")
    return {"id": candidate.id, "action": action, "level": candidate.level,
            "next_checkpoint": min(20, (candidate.level // 4 + 1) * 4),
            "random_rolls_left": remaining, "probability_lower": float(p_lo),
            "probability_upper": float(p_hi), "probability_fraction_lower": str(p_lo),
            "score": [float(current.lo), float(current.hi)],
            "baseline": [float(baseline.lo), float(baseline.hi)],
            "terminal_score_needed": [float(baseline.lo - complement.hi), float(baseline.hi - complement.lo)]}
