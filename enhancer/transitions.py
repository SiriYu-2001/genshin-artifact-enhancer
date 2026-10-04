"""Pure deterministic validation of observed enhancement results."""
from dataclasses import replace
from fractions import Fraction as F
from functools import lru_cache
from itertools import product
import re

from .execution import GuardError
from .model import ROLLS, Stat, UncertainObservation


@lru_cache(maxsize=60)
def sums(key, count):
    totals = {F(0)}
    for _ in range(count):
        totals = {value + roll for value in totals for roll in ROLLS[key]}
    return totals


def enhancement_result(previous, observation, text, profile):
    if observation.level < previous.level or observation.level > 20:
        raise GuardError("Impossible level change")
    values = []
    for i, old in enumerate(previous.stats):
        # The game keeps an up-arrow next to the roll that just increased.
        # Accept that decoration, but never extract a number from arbitrary text.
        raw = text[f"sub_value_{i}"].strip().lstrip("+·•- ：:`、").replace(",", "")
        raw = raw.removesuffix("↑").rstrip().removesuffix("%")
        if not re.fullmatch(r"\d+(?:\.\d+)?", raw):
            raise GuardError(f"Unreadable substat {i}")
        values.append(Stat(old.key, F(raw), old.pending and observation.level < 4))
    updated = replace(previous, level=observation.level, stats=tuple(values))
    try:
        profile.score(updated)  # All values must correspond to legal roll sums.
    except UncertainObservation as exc:
        raise GuardError(str(exc)) from exc
    nodes = updated.level // 4 - previous.level // 4
    random_nodes = nodes - int(any(s.pending for s in previous.stats) and updated.level >= 4)
    if random_nodes < 0:
        raise GuardError("Invalid activation transition")
    possible_counts = []
    for old, new in zip(previous.stats, updated.stats):
        difference = new.value - old.value
        tolerance = F("0.1001") if old.key.endswith("_") else F("1.0001")
        counts = [0] if difference == 0 else []
        counts += [n for n in range(1, random_nodes + 1)
                   if any(abs(difference - gain) <= tolerance for gain in sums(old.key, n))]
        possible_counts.append(counts)
    if not any(sum(counts) == random_nodes for counts in product(*possible_counts)):
        raise GuardError("Observed stats do not match the number of crossed enhancement nodes")
    return updated
