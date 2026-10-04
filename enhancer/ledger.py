"""Resource accounting from observed before/after balances, not planned material EXP."""
from dataclasses import dataclass
from .execution import GuardError


@dataclass(frozen=True)
class Balances:
    mora: int
    exp_items: dict[str, int]
    artifact_counts: dict[int, int]


def resource_delta(before, after):
    if before.mora < after.mora:
        raise GuardError("Mora increased: unrelated activity invalidates isolated accounting")
    if set(before.exp_items) != set(after.exp_items) or set(before.artifact_counts) != set(after.artifact_counts):
        raise GuardError("Incomplete resource snapshots")
    if before.artifact_counts.get(5) != after.artifact_counts.get(5) or 5 not in before.artifact_counts:
        raise GuardError("Five-star inventory count changed or is unknown")
    delta = {
        "mora": before.mora - after.mora,
        "exp_items": {key: before.exp_items[key] - after.exp_items[key] for key in before.exp_items},
        "artifact_materials": {star: before.artifact_counts[star] - after.artifact_counts[star]
                               for star in before.artifact_counts},
    }
    # Some EXP may be returned when reaching +20. Record signed item differences.
    if any(n < 0 for n in delta["artifact_materials"].values()):
        raise GuardError("Artifact inventory grew: scan completeness or unrelated activity")
    return delta
