"""Serial execution invariants. This module contains no mouse input functions."""
from dataclasses import dataclass
from enum import Enum


class GuardError(RuntimeError):
    pass


@dataclass(frozen=True)
class Material:
    kind: str
    rarity: int | None
    name: str
    quantity: int
    observed: bool


def validate_material_batch(materials, *, expected_slots, complete, fresh, target_verified):
    if not complete or not fresh or not target_verified or not materials or len(materials) != expected_slots:
        raise GuardError("incomplete/stale material batch or unverified target")
    for material in materials:
        if not material.observed or material.quantity < 1:
            raise GuardError("unknown material")
        if material.kind == "artifact":
            if material.rarity not in (1, 2, 3, 4):
                raise GuardError("five-star or unknown artifact material is forbidden")
        elif material.kind == "exp_item":
            if material.name not in ("祝圣油膏", "祝圣精华"):
                raise GuardError("unknown EXP item")
        else:
            raise GuardError("unknown material kind")


@dataclass(frozen=True)
class Progress:
    identity: str
    level: int
    exp: int | None


def compare_progress(before, after):
    if before.identity != after.identity or not 0 <= before.level <= after.level <= 20:
        raise GuardError("target changed or impossible level transition")
    if after.level == 20 and before.level < 20:
        return "complete"
    if after.level // 4 > before.level // 4:
        return "reevaluate"
    if after.level > before.level:
        return "add_materials"
    if before.exp is None or after.exp is None:
        return "reread"
    if after.exp > before.exp:
        return "add_materials"
    if after.exp < before.exp:
        raise GuardError("EXP decreased without a level increase")
    return "reread"


class SerialBatch:
    """At most one consuming action in flight; timeouts never authorize resending."""
    def __init__(self):
        self.pending_id = None
        self.used_ids = set()

    def submit(self, operation_id, materials, **evidence):
        if self.pending_id is not None or operation_id in self.used_ids:
            raise GuardError("pending or repeated consuming operation")
        validate_material_batch(materials, **evidence)
        self.pending_id = operation_id
        self.used_ids.add(operation_id)

    def submit_stage(self, operation_id, proof, count, visible, *, fresh, target_verified):
        from .stage_proof import validate_restricted_stage
        if self.pending_id is not None or operation_id in self.used_ids:
            raise GuardError('pending or repeated consuming operation')
        if not fresh or not target_verified:raise GuardError('stale or wrong target')
        validate_restricted_stage(proof,count,visible)
        self.pending_id=operation_id
        self.used_ids.add(operation_id)

    def observe(self, operation_id, before, after):
        if operation_id != self.pending_id:
            raise GuardError("wrong operation result")
        action = compare_progress(before, after)
        if action != "reread":
            self.pending_id = None
        return action
