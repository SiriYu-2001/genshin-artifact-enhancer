"""Bounded one-item experiment. One verified EXP essence per serial material batch.

Starts in the artifact bag on the verified target. No automatic artifact fodder,
unlocking, equipping, rare consumables, or fallback input paths exist here.
"""
import argparse
from dataclasses import asdict, replace
from datetime import datetime
from fractions import Fraction as F
import json
from pathlib import Path
import re
import time
import uuid

import numpy as np
from PIL import Image

from .capped import CappedInventory
from .execution import GuardError, Material, SerialBatch, Progress
from .model import Profile, Stat, evaluate
from .navigation import Navigation, ROOT
from .observations import EnhancementObservation, integer
from .report import load_scan

_active_path = ROOT / "runtime/active-run.json"
ACTIVE = json.loads(_active_path.read_text(encoding="utf-8")) if _active_path.exists() else {}
SCAN = Path(ACTIVE.get("scan_directory", ROOT / "runtime/no-scan"))
RUN = Path(ACTIVE.get("run_directory", ROOT / "runtime/round1-live"))
TARGET_ID = ACTIVE.get("target_id", "")
ICON_BOX = (1198, 838, 1255, 899)


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


class Experiment:
    def __init__(self):
        RUN.mkdir(exist_ok=True)
        if not TARGET_ID:
            raise GuardError("No prepared run. Use the prepare command first.")
        self.profile = Profile.load(ACTIVE.get("profile", ROOT / "profiles/木偶.json"))
        if ACTIVE.get("ownership") == "no-borrow":
            self.profile.data["allowed_equipped_characters"] = self.profile.data["character_aliases"]
        self.inventory, self.names, coverage = load_scan(SCAN)
        if not coverage["complete"]:
            raise GuardError("Incomplete baseline")
        if ACTIVE.get("inventory_updates_path"):
            from .batch import apply_updates
            updates = json.loads(Path(ACTIVE["inventory_updates_path"]).read_text(encoding="utf-8"))
            self.inventory = apply_updates(self.inventory, updates)
        self.target = next(a for a in self.inventory if a.id == TARGET_ID)
        if (RUN / "target-state.json").exists():
            state = json.loads((RUN / "target-state.json").read_text(encoding="utf-8"))
            self.target = replace(self.target, level=state["level"], stats=tuple(Stat.from_dict(s) for s in state["substats"]))
        if (RUN/'identity.json').exists():
            identity=json.loads((RUN/'identity.json').read_text(encoding='utf-8'))
            if identity['id']!=TARGET_ID:raise GuardError('Identity evidence mismatch')
            self.target=replace(self.target,equipped=identity['equipped'])
        self.nav = Navigation()
        self.serial = SerialBatch()
        self.baseline = CappedInventory(self.inventory, self.profile)
        self.receipt_roots = list(dict.fromkeys([*ACTIVE.get("receipt_roots", []), str(RUN)]))

    def read_good(self):
        session = self.nav.session()
        request_id = uuid.uuid4().hex
        directory = Path(session["directory"])
        request = directory / f"requests/{request_id}.json"
        temp = request.with_suffix(".tmp")
        save(temp, {"id": request_id, "kind": "read-current", "layout": "capture"})
        temp.replace(request)
        response_path = directory / f"responses/{request_id}.json"
        until = time.monotonic() + 50
        while time.monotonic() < until:
            if response_path.exists():
                response = json.loads(response_path.read_text(encoding="utf-8-sig"))
                if response["state"] == "exited":
                    if response.get("exitCode") != 0:
                        raise GuardError("yas observation failed")
                    data = json.loads((Path(response["directory"]) / "enhancer-artifacts.json").read_text(encoding="utf-8"))
                    if len(data) != 1:
                        raise GuardError("Expected one current artifact")
                    return data[0]
                if response["state"] == "failed":
                    raise GuardError("yas observation failed")
            time.sleep(.1)
        raise GuardError("yas read timed out")

    def verify_current(self, *, after=False):
        self.nav.ensure_bag()
        item = self.read_good()
        if (item["name"], item["setKey"], item["slotKey"], item["mainStatKey"], item["rarity"]) != (
                self.names[TARGET_ID], self.target.set_key, self.target.slot, self.target.main, 5):
            raise GuardError("Wrong artifact identity")
        observed = replace(self.target, level=item["level"], stats=tuple(Stat.from_dict(s) for s in item["substats"]))
        observed.validate()
        previous = {s.key: s.value for s in self.target.stats}
        current = {s.key: s.value for s in observed.stats}
        if not after and (observed.level != self.target.level or current != previous):
            raise GuardError("Artifact changed outside this experiment")
        if after and (observed.level < self.target.level or set(current) != set(previous)
                      or any(current[k] < previous[k] for k in current)):
            raise GuardError("Impossible artifact change")
        return observed, item

    def observe_enhancement(self):
        return self.nav.observe_validated('enhance-1920',
            lambda data:(EnhancementObservation.parse(data),data),
            attempts=4,interval=.3,retry_on=(GuardError,))

    def park_cursor(self):
        self.nav._navigation_click("dismiss_material_tooltip", 1750, 620)
        win = self.nav.observation["window"]
        self.nav.api("POST", f'/api/SetCursorPos?x={win["left"]+1148}&y={win["top"]+655}')
        time.sleep(.8)

    def confirm_checked(self, before, prepared, fields, proof, template, stock, operation_id):
        if prepared.material_slots_used != 1 or prepared.mora_cost != 10000:
            raise GuardError("Only one verified 10000 EXP essence is supported")
        if fields["material_count_check"].replace(" ", "") != f"装备强化消耗(1/{prepared.material_capacity})":
            raise GuardError("Material count readings disagree")
        qty = re.fullmatch(r"1/([0-9]+)", fields["selected_quantity"].strip())
        if not qty or int(qty[1]) != stock:
            raise GuardError("Selected EXP stack count not verified")
        if prepared.level != before.level or prepared.exp != before.exp or prepared.mora != before.mora:
            raise GuardError("Target/resources changed during material selection")
        if proof["name"] != "祝圣精华" or proof["kind"] != "圣遗物强化素材":
            raise GuardError("EXP material identity not verified")
        for i, stat in enumerate(self.target.stats):
            shown = fields[f"sub_value_{i}"].strip().lstrip("+·•- ").removesuffix("%")
            if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", shown) or abs(F(shown) - stat.value) > F(1, 100000):
                raise GuardError(f"Target substat {i} changed or unreadable: {shown}")
        with Image.open(self.nav.directory / "game.png") as image:
            actual = np.asarray(image.crop(ICON_BOX), dtype=np.int16)
        error = float(np.mean(np.abs(template - actual)))
        # Diagnostic only: hover/tooltip shading changes the same icon's brightness.
        # Identity is established by the freshly OCR-verified source name, exactly
        # one occupied tray slot, its 1/stock stack label, and the 10000 EXP cost.
        fresh = time.monotonic() - self.nav.observed_at < 3
        self.serial.submit(operation_id, [Material("exp_item", None, "祝圣精华", 1, True)],
                           expected_slots=1, complete=True, fresh=fresh, target_verified=True)
        pending = {"id": operation_id, "before": asdict(before), "material": "祝圣精华", "quantity": 1,
                   "stock_before": stock, "target_id": TARGET_ID, "icon_error": error, "evidence": str(self.nav.directory)}
        save(RUN / "pending.json", pending)
        # The consuming point is private to this guard. No generic confirm action is exposed.
        win = self.nav.observation["window"]
        self.nav.api("POST", f'/api/SetCursorPos?x={win["left"]+1743}&y={win["top"]+1019}')
        self.nav.api("POST", "/api/mouse_event?dwFlags=2&dx=0&dy=0&dwData=0")
        time.sleep(.08)
        self.nav.api("POST", "/api/mouse_event?dwFlags=4&dx=0&dy=0&dwData=0")
        return pending

    def run_one(self):
        if (RUN / "pending.json").exists():
            raise GuardError("An earlier consuming operation requires reconciliation; no repeat is allowed")
        self.target, _ = self.verify_current()
        decision = evaluate(self.target, self.inventory, self.profile, self.baseline)
        if decision["action"] != "enhance":
            return {"stopped": True, "decision": decision}
        self.nav.ensure_bag()
        if self.nav.observation["text"]["action"] != "强化":
            raise GuardError("Enhancement entry unavailable")
        self.nav._navigation_click("open_enhancement", 1700, 1018)
        before, _ = self.observe_enhancement()
        if before.level != self.target.level or before.material_slots_used != 0 or self.names[TARGET_ID] not in before.title:
            raise GuardError("Unexpected initial enhancement state")
        self.nav._navigation_click("open_material_picker", 1228, 868)
        picker = self.nav.observe("material-picker-1920")["text"]
        stock = integer(picker["essence_stock"])
        if stock <= 0 or picker["material_count"].replace(" ", "") != "装备强化消耗(0/15)":
            raise GuardError("No clean EXP-material selection")
        self.nav._navigation_click("inspect_exp_stack", 100, 177)
        proof = self.nav.observe("material-tooltip-1920")["text"]
        if proof["name"] != "祝圣精华" or proof["kind"] != "圣遗物强化素材":
            raise GuardError("Unexpected material; nothing will be consumed")
        with Image.open(self.nav.directory / "game.png") as image:
            template = np.asarray(image.crop(ICON_BOX), dtype=np.int16)
        self.park_cursor()
        prepared, fields = self.observe_enhancement()
        operation_id = uuid.uuid4().hex
        pending = self.confirm_checked(before, prepared, fields, proof, template, stock, operation_id)
        time.sleep(2)
        until = time.monotonic() + 25
        after = None
        stable = 0
        while time.monotonic() < until:
            try:
                observation, _ = self.observe_enhancement()
                if observation.mora == before.mora - 10000 and observation.material_slots_used == 0:
                    after = observation
                    stable += 1
                    if stable >= 2:
                        break
                else:
                    stable = 0
            except GuardError:
                stable = 0
            time.sleep(.5)
        if after is None or stable < 2:
            raise GuardError("Consumption result not verified; pending operation kept for reconciliation")
        self.nav._navigation_click("close_material_picker", 1840, 48)
        actual, item = self.verify_current(after=True)
        action = self.serial.observe(operation_id, Progress(TARGET_ID, before.level, before.exp),
                                     Progress(TARGET_ID, actual.level, after.exp if actual.level == after.level else None))
        if action == "reread":
            raise GuardError("Progress not verified; pending operation kept")
        item["index"] = int(TARGET_ID.rsplit(":",1)[1])
        item["lock"] = self.target.locked  # Current-only yas does not read the lock icon.
        save(RUN / "target-state.json", item)
        receipt = dict(pending, after=asdict(after), actual_artifact=item, progress_action=action,
                       decision_before=decision, finished=datetime.now().isoformat(), mora_spent=10000)
        save(RUN / f"receipt-{operation_id}.json", receipt)
        (RUN / "pending.json").unlink()
        self.target = actual
        return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batches", type=int, default=1, choices=range(1, 31))
    args = parser.parse_args()
    run = Experiment()
    for _ in range(args.batches):
        try:
            receipt = run.run_one()
        except Exception as exc:
            save(RUN / "last-error.json", {"error": str(exc), "observation": run.nav.observation,
                                           "evidence": str(run.nav.directory), "time": datetime.now().isoformat()})
            if not (RUN / "pending.json").exists():
                try:
                    current = run.nav.observe("enhance-1920")["text"]
                    if current["confirm"] == "强化" and "装备强化消耗" in current["material_count"]:
                        run.nav._navigation_click("close_material_picker", 1840, 48)
                except Exception:
                    pass
            raise
        print(json.dumps(receipt, ensure_ascii=False), flush=True)
        if receipt.get("stopped") or run.target.level == 20:
            break


if __name__ == "__main__":
    main()
