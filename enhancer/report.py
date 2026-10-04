"""Build a reviewable baseline from a complete enhanced-yas scan (never ordinary GOOD alone)."""
import argparse
from copy import deepcopy
import json
from dataclasses import asdict
from fractions import Fraction as F
from pathlib import Path
import hashlib

from .model import Artifact, Bounds, InventoryEvaluation, Profile, SLOTS, Stat, UncertainObservation, best_build, evaluate


def load_scan(directory):
    directory = Path(directory)
    raw = json.loads((directory / "enhancer-artifacts.json").read_text(encoding="utf-8-sig"))
    scan_count = json.loads((directory / "scan-count.json").read_text(encoding="utf-8-sig"))["requested"]
    artifacts, names, failures = [], {}, []
    override_path = directory / "ownership-overrides.json"
    overrides = json.loads(override_path.read_text(encoding="utf-8")) if override_path.exists() else {}
    seen = set()
    for entry in raw:
        identifier = f'{directory.name}:{entry["index"]}'
        if entry["index"] in seen:
            failures.append({"index": entry["index"], "reason": "duplicate scan position"})
            continue
        seen.add(entry["index"])
        equip = entry["equip_raw"].strip()
        if equip in ("来源", "源"):
            # yas's fixed footer crop can overlap the bag's Source label when unequipped.
            equip = ""
        elif equip:
            equip = equip.removesuffix("已装备").strip() if equip.endswith("已装备") else f"UNKNOWN:{equip}"
        override = overrides.get(str(entry["index"]))
        if override:
            digest = hashlib.sha256(json.dumps(entry, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if digest != override["record_sha256"]:
                raise ValueError("Ownership override belongs to a different observation")
            equip = override["equipped"]
        a = Artifact(identifier, entry["setKey"], entry["slotKey"], entry["mainStatKey"], entry["level"],
                     tuple(Stat.from_dict(s) for s in entry["substats"]), entry["rarity"], equip,
                     entry["lock"], entry.get('enhancement_kind',"defined" if entry["special"] else "ordinary"),
                     entry.get('excluded',False))
        artifacts.append(a)
        names[identifier] = entry["name"]
    missing = sorted(set(range(1, scan_count + 1)) - seen)
    extra = sorted(seen - set(range(1, scan_count + 1)))
    return artifacts, names, {"requested": scan_count, "recognized": len(raw),
                              "missing_positions": missing, "extra_positions": extra,
                              "errors": failures, "complete": not missing and not extra and not failures}


def displayed_score(artifact, profile):
    from .model import MEANS
    return sum(profile.weight(s.key) * s.value / MEANS[s.key] for s in artifact.stats if not s.pending)


def select_build(pool, profile):
    if profile.data.get("artifact_crit_rate_cap") is not None:
        from .capped import CappedInventory
        prepared = CappedInventory(pool, profile)
        lookup = {a.id: a for a in pool}
        return [lookup[k] for k in prepared.best_ids] if prepared.best_ids else None
    choices = []
    for off in SLOTS:
        build = []
        for slot in SLOTS:
            candidates = [a for a in pool if a.level == 20 and profile.allows(a) and a.slot == slot
                          and (slot == off or a.set_key == profile.data["set_key"])]
            if not candidates:
                break
            build.append(max(candidates, key=lambda a: displayed_score(a, profile)))
        else:
            choices.append(build)
    return max(choices, key=lambda build: sum(displayed_score(a, profile) for a in build)) if choices else None


def create_report(directory, profile, include_candidates=False):
    artifacts, names, coverage = load_scan(directory)
    valid, errors = [], []
    for a in artifacts:
        if a.rarity != 5 or not profile.allows(a):
            continue
        try:
            profile.score(a)
            valid.append(a)
        except UncertainObservation as exc:
            errors.append({"id": a.id, "name": names[a.id], "level": a.level, "reason": str(exc)})
    best = select_build(valid, profile)
    wearing = [a for a in valid if a.equipped in profile.data.get("character_aliases", [profile.data["character"]])]

    def describe(build):
        if not build:
            return None
        bounds = sum((profile.score(a, include_pending=False) for a in build), Bounds(F(0), F(0)))
        shown_score = sum(displayed_score(a, profile) for a in build)
        from .capped import main_crit
        total_crit = sum(main_crit(a) + sum(s.value for s in a.stats if s.key == "critRate_" and not s.pending) for a in build)
        if profile.data.get("artifact_crit_rate_cap") is not None:
            from .capped import build_score
            bounds = build_score(build, profile)
            shown_score = build_score(build, profile, displayed=True)
        return {"displayed_score": float(shown_score), "artifact_crit_rate_total": float(total_crit),
                "internal_score_bounds": [float(bounds.lo), float(bounds.hi)],
                "items": [{"id": a.id, "name": names[a.id], "slot": a.slot, "set": a.set_key,
                           "level": a.level, "equipped": a.equipped, "locked": a.locked,
                           "score": float(displayed_score(a, profile)),
                           "substats": [{"key": s.key, "value": float(s.value), "pending": s.pending} for s in a.stats]}
                          for a in build]}

    baseline_errors = [e for e in errors if e["level"] == 20]
    result = {"profile": profile.data, "coverage": coverage, "recognition_errors": errors,
              "baseline_usable": coverage["complete"] and not baseline_errors and best is not None,
              "best_available_build": describe(best), "equipped_items_found": describe(wearing),
              "equipped_build_complete": len(wearing) == 5 and {a.slot for a in wearing} == set(SLOTS),
              "five_star_count": sum(a.rarity == 5 for a in artifacts),
              "profile_candidate_count": sum(a.level < 20 for a in valid)}
    if include_candidates and result["baseline_usable"]:
        if profile.data.get("artifact_crit_rate_cap") is not None:
            from .capped import CappedInventory
            prepared = CappedInventory(valid, profile)
        else:
            prepared = InventoryEvaluation(valid, profile)
        result["candidates"] = [dict(evaluate(a, valid, profile, prepared), name=names[a.id])
                                for a in valid if a.level < 20]
        result["candidates"].sort(key=lambda a: a.get("probability_lower", -1), reverse=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scan_directory")
    parser.add_argument("--profile", default=str(Path(__file__).resolve().parents[1] / "profiles/木偶.json"))
    parser.add_argument("--candidates", action="store_true")
    parser.add_argument("--output", required=True)
    parser.add_argument("--ownership", choices=["configured", "borrow", "no-borrow"], default="configured")
    args = parser.parse_args()
    profile = Profile.load(args.profile)
    if args.ownership == "borrow":
        profile.data["allowed_equipped_characters"] = ["*"]
    elif args.ownership == "no-borrow":
        profile.data["allowed_equipped_characters"] = profile.data.get("character_aliases", [profile.data["character"]])
    report = create_report(args.scan_directory, profile, args.candidates)
    report["ownership_mode"] = args.ownership
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"baseline_usable": report["baseline_usable"], "coverage": report["coverage"],
                      "five_star_count": report["five_star_count"],
                      "profile_candidate_count": report["profile_candidate_count"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
