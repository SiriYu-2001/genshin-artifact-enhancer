"""Use the game's stage-add function; verify every selected artifact's visible stars."""
from dataclasses import asdict, replace
from datetime import datetime
import colorsys
import json
import re
import time
import uuid

import cv2
import numpy as np
from PIL import Image

from .execution import GuardError, Material, SerialBatch, Progress
from .experiment import Experiment, RUN, save, TARGET_ID, ACTIVE
from pathlib import Path
from .navigation import ROOT
from .model import evaluate
from .transitions import enhancement_result
from .stage_proof import StageProof,infer_stage_batch
from .text_identity import canonical,title_has_name


def verify_stage_settings(run):
    for attempt in range(3):
        text=run.nav.observe("material-settings-1920")["text"]
        if text["heading"]=="放入设置":break
        run.observe_enhancement()
        run.nav._navigation_click("open_material_settings",1608,768)
        try:
            text=run.nav.wait_state("material-settings-1920",lambda t:t["heading"]=="放入设置",timeout=2)
            break
        except RuntimeError:
            if attempt==2:raise
    if "5星" not in text["five_label"] or "圣遗物" not in text["five_label"]:
        raise GuardError("Five-star setting label not verified")
    with Image.open(run.nav.directory/"game.png") as image:
        color=np.median(np.asarray(image.convert("RGB"))[449:465,1238:1253],axis=(0,1))
    if not (color.max()<140 and color[2]>color[0]+5):
        raise GuardError("Five-star quick-add is enabled or unreadable")
    evidence=str(run.nav.directory)
    run.nav._navigation_click("close_material_settings",1347,300)
    state,fields=run.observe_enhancement()
    if state.material_slots_used!=0 or fields["material_filter"]!="4星及以下素材" or fields["stage_add"]!="阶段放入":
        raise GuardError("Stage selection preconditions changed")
    return StageProof(True,4,True,True,evidence)


def visible_stars(image, left):
    crop = np.asarray(image.crop((int(left), 898, int(left)+99, 919)).convert("RGB"))
    hsv = cv2.cvtColor(crop, cv2.COLOR_RGB2HSV)
    mask = ((hsv[:,:,0] > 14) & (hsv[:,:,0] < 40) & (hsv[:,:,1] > 120) & (hsv[:,:,2] > 170)).astype(np.uint8)
    _, _, boxes, _ = cv2.connectedComponentsWithStats(mask, 8)
    substantial = [b for b in boxes[1:] if b[4] > 50]
    if len(substantial) != 1:
        raise GuardError("Selected material stars are unreadable")
    x,y,w,h,area = map(int, substantial[0])
    stars = round(w / 15.5)
    if not (1 <= stars <= 5 and abs(w - stars*15.5) <= 3 and 12 <= h <= 18 and abs(area-stars*126) <= stars*30 and abs(x+w/2-49.5)<=6):
        raise GuardError(f"Unrecognized star pattern: {list(substantial[0])}")
    if stars == 5:
        raise GuardError("Five-star material: confirmation forbidden")
    return stars


def drag_tray(run, distance):
    win=run.nav.observation["window"]
    start=1250 if distance<0 else 1700
    run.nav.api("POST",f'/api/SetCursorPos?x={win["left"]+start}&y={win["top"]+875}')
    time.sleep(.08)
    run.nav.api("POST","/api/mouse_event?dwFlags=2&dx=0&dy=0&dwData=0")
    time.sleep(.08)
    for step in range(1,9):
        run.nav.api("POST",f'/api/SetCursorPos?x={win["left"]+start-round(distance*step/8)}&y={win["top"]+875}')
        time.sleep(.07)
    time.sleep(.8)  # Let drag velocity settle, so inertia cannot skip unobserved slots.
    run.nav.api("POST","/api/mouse_event?dwFlags=4&dx=0&dy=0&dwData=0")
    time.sleep(.65)


def tray_centers(image):
    array=np.asarray(image.convert("RGB"))
    mask=array[936,1174:1890].min(axis=1)>180
    centers=[]
    start=None
    for i,value in enumerate(list(mask)+[False]):
        if value and start is None:start=i
        elif not value and start is not None:
            if 90<=i-start<=110:centers.append(1174+(start+i)/2)
            start=None
    if len(centers)<min(3,6):raise GuardError("Material card boundaries are unreadable")
    return centers


def tray_phase(centers):
    pitch=111.3
    phases=np.array([(1226.5-x)%pitch for x in centers])
    origin=phases[0]
    adjusted=origin+(phases-origin+pitch/2)%pitch-pitch/2
    if adjusted.std()>2:raise GuardError("Material row spacing is inconsistent")
    return float(np.median(adjusted)%pitch)


def read_tray_count(run,expected):
    for _ in range(4):
        text=run.nav.observe("tray-1920")["text"]
        counts=[]
        for value in text.values():
            match=re.fullmatch(r"装备强化消耗[（(](\d+)/(\d+)[）)]",value.replace(" ",""))
            if match:counts.append(int(match[1]))
        if counts and all(n==expected for n in counts):return
        time.sleep(.15)
    raise GuardError("Material count changed or could not be read during inspection")


def verify_whole_tray(run,count):
    if not 1<=count<=15:raise GuardError("Invalid tray count")
    if count>6:
        drag_tray(run,-600)
        drag_tray(run,-600)
        read_tray_count(run,count)
    found,evidence={},[]
    offset=0.0
    previous_phase=None
    for page in range(30):
        if page:
            # Less than one card per drag, so its measured phase is unambiguous.
            drag_tray(run,50)
            read_tray_count(run,count)
        with Image.open(run.nav.directory/"game.png") as image:
            centers=tray_centers(image)
            phase=tray_phase(centers)
            if previous_phase is None:
                if min(phase,111.3-phase)>3:raise GuardError("Tray did not reset to its start")
            else:
                delta=(phase-previous_phase)%111.3
                if not 2<delta<107:raise GuardError("Tray motion was not observed reliably")
                offset+=delta
            for center in centers:
                index=round((center+offset-1226.5)/111.3)
                if not 0<=index<count:continue
                star=visible_stars(image,round(center-49.5))
                if index in found and found[index]!=star:raise GuardError("Material rarity readings disagree")
                found[index]=star
        evidence.append(str(run.nav.directory))
        if set(found)==set(range(count)):return [found[i] for i in range(count)],evidence
        previous_phase=phase
    raise GuardError("Not every material was verified")


def finish_receipt(run, pending, after, actual):
    progress=run.serial.observe(pending["id"],Progress(TARGET_ID,pending["before"]["level"],pending["before"]["exp"]),
                                Progress(TARGET_ID,actual.level,after.exp))
    if progress=="reread": raise GuardError("Progress not established")
    item={"index":int(TARGET_ID.rsplit(":",1)[1]),"name":run.names[TARGET_ID],
          "setKey":actual.set_key,"slotKey":actual.slot,"mainStatKey":actual.main,
          "rarity":5,"level":actual.level,"lock":actual.locked,"equipped":actual.equipped,"equip_raw":"",
          "special":False,"source":"yas_enhancement_page",
          "substats":[{"key":s.key,"value":float(s.value),"pending":s.pending} for s in actual.stats]}
    save(RUN/"target-state.json",item)
    receipt = dict(pending,after=asdict(after),actual_artifact=item,progress_action=progress,finished=datetime.now().isoformat())
    save(RUN/f'receipt-{pending["id"]}.json',receipt)
    (RUN/"pending.json").unlink()
    run.target=actual
    return receipt


def confirm_visible_stage(run, before,proof):
    if (RUN/"pending.json").exists():
        raise GuardError("Pending earlier operation; no repeat")
    prepared, fields=run.observe_enhancement()
    count=prepared.material_slots_used
    if not 1<=count<=15:
        raise GuardError("No valid materials selected")
    if not title_has_name(fields["title"],run.names[TARGET_ID]) or prepared.level!=run.target.level:
        raise GuardError("Unexpected enhancement target")
    if prepared.exp is not None and before.exp is not None and prepared.exp!=before.exp:
        raise GuardError('Target EXP changed during selection')
    with Image.open(run.nav.directory/"game.png") as image:
        visible=[visible_stars(image,round(1177+i*111.3)) for i in range(min(count,6))]
    evidence=[proof.settings_evidence,str(run.nav.directory)]
    verified,_=run.observe_enhancement()
    if verified.material_slots_used!=count or verified.level!=before.level:
        raise GuardError("Material batch changed after inspection")
    operation=uuid.uuid4().hex
    if (ROOT/'runtime/stop.signal').exists():raise GuardError('Stop requested')
    run.serial.submit_stage(operation,proof,count,visible,
                      fresh=time.monotonic()-run.nav.observed_at<3,target_verified=True)
    pending={"id":operation,"before":asdict(before),"material":"restricted-stage selection","slots":count,
             "visible_rarities":visible,
             "target_id":TARGET_ID,"evidence":evidence,"method":"stage_add","selection_proof":asdict(proof)}
    save(RUN/"pending.json",pending)
    win=run.nav.observation["window"]
    game=[w for w in run.nav.api("GET","/api/windows") if w["title"]=="原神" and w["classname"]=="UnityWndClass"]
    if len(game)!=1: raise GuardError("Game window ambiguous")
    run.nav.api("PATCH",f'/api/windows/{game[0]["hWnd"]}')
    time.sleep(.2)
    run.nav.api("POST",f'/api/SetCursorPos?x={win["left"]+1743}&y={win["top"]+1019}')
    time.sleep(.08)
    run.nav.api("POST","/api/mouse_event?dwFlags=2&dx=0&dy=0&dwData=0")
    time.sleep(.08)
    run.nav.api("POST","/api/mouse_event?dwFlags=4&dx=0&dy=0&dwData=0")
    time.sleep(2)
    until=time.monotonic()+20
    error=None
    while time.monotonic()<until:
        try:
            after,fields=run.observe_enhancement()
            if after.material_slots_used!=0 and after.level!=20:
                raise GuardError("Enhancement result not stable")
            if (after.level,after.exp or 0)<=(before.level,before.exp or 0):
                raise GuardError("EXP result not stable")
            actual=enhancement_result(run.target,after,fields,run.profile)
            return finish_receipt(run,pending,after,actual)
        except GuardError as exc:
            error=exc
            time.sleep(.3)
    raise GuardError(f"Pending result needs reread; do not repeat: {error}")


def raw_matches(raw, target, name):
    labels={"atk_":"攻击力","atk":"攻击力","critRate_":"暴击率","critDMG_":"暴击伤害",
            "enerRech_":"元素充能效率","eleMas":"元素精通","hp":"生命值","hp_":"生命值","def":"防御力","def_":"防御力",
            "pyro_dmg_":"火元素伤害加成","hydro_dmg_":"水元素伤害加成","cryo_dmg_":"冰元素伤害加成",
            "electro_dmg_":"雷元素伤害加成","anemo_dmg_":"风元素伤害加成","geo_dmg_":"岩元素伤害加成",
            "dendro_dmg_":"草元素伤害加成","physical_dmg_":"物理伤害加成","heal_":"治疗加成"}
    expected=[labels[s.key]+"+"+format(float(s.value),".1f" if s.key.endswith("_") else ".0f")+("%" if s.key.endswith("_") else "") for s in target.stats]
    return (canonical(raw["name"])==canonical(name) and raw["level"]==target.level and raw["star"]==5
            and raw['main_stat_name']==labels.get(target.main)
            and tuple(raw.get('pending',[]))==tuple(s.pending for s in target.stats)
            and [s.replace(",","") for s in raw["sub_stat"]]==expected)


def enter_target(run):
    from .sets import SET_LABELS
    labels=dict(SET_LABELS)
    labels[run.profile.data['set_key']]=run.profile.data['set_label']
    label=labels.get(run.target.set_key)
    if label is None:raise GuardError(f"Missing set label: {run.target.set_key}")
    settings=run.nav.observe("material-settings-1920")["text"]
    if settings["heading"]=="放入设置":
        run.nav._navigation_click("close_material_settings",1347,300)
    bag=run.nav.observe("bag-1920")["text"]
    if bag["page"]!="圣遗物":
        sets=run.nav.observe("sets-1920")["text"]
        if "圣遗物套装筛选" in sets["heading"]:
            run.nav.select_open_set(label)
            return enter_target(run)
        dialog=run.nav.observe("filter-1920")["text"]
        if dialog["heading"]=="圣遗物筛选":
            run.nav.clear_open_filter()
            return enter_target(run)
    if bag["page"]=="圣遗物" and re.search(r"\d+/\d+",bag["inventory_count"]):
        if (not raw_matches(run.nav.read_current(),run.target,run.names[TARGET_ID])
                or (run.target.equipped.startswith('UNKNOWN:') and not (RUN/'identity.json').exists())):
            run.nav.ensure_bag()
            run.nav._navigation_click("open_filter",167,1018)
            run.nav.wait_state("filter-1920",lambda t:t["heading"]=="圣遗物筛选")
            run.nav._navigation_click("reset_filter",165,1020)
            run.nav.observe("filter-1920")
            run.nav._navigation_click("open_set_filter",312,204)
            run.nav.select_open_set(label,target=run.target)
            ordered=[a for a in run.inventory if a.set_key==run.target.set_key and a.level<20
                     and (not run.target.locked or a.locked)
                     and (bool(run.target.equipped and not run.target.equipped.startswith('UNKNOWN:'))
                          or not a.equipped or a.equipped.startswith('UNKNOWN:'))]
            position=next((i for i,a in enumerate(ordered) if a.id==TARGET_ID),0)
            jumped=position>=32
            if jumped:run.nav.scroll_grid(max(1,position//8-1)*10)
            found=False
            previous=None
            for page in range(30):
                signatures=[]
                for x,y in run.nav.grid_points():
                    if time.monotonic()-run.nav.observed_at>3:
                        run.nav.ensure_bag()
                    run.nav._navigation_click("select_grid_item",x,y)
                    raw=run.nav.read_current()
                    signatures.append(json.dumps(raw,sort_keys=True,ensure_ascii=False))
                    if raw_matches(raw,run.target,run.names[TARGET_ID]):
                        if not run.target.equipped or run.target.equipped.startswith("UNKNOWN:"):
                            run.target=replace(run.target,equipped="")
                        save(RUN/'identity.json',{'id':TARGET_ID,'equipped':run.target.equipped,'evidence':str(run.nav.item_directory),'filter':'set, locked-if-locked, nonmax, idle-if-unknown'})
                        found=True
                        break
                if found:break
                if jumped and page==1:
                    # Approximate scan order is only a navigation hint. If it
                    # misses, return to the top and verify the entire set.
                    run.nav.scroll_grid(-max(160,position//8*12))
                    previous=None
                    continue
                signature=frozenset(signatures)
                if signature==previous:break
                previous=signature
                run.nav.scroll_grid()
            if not found: raise GuardError("Saved target not found in filtered grid; no consumption")
        run.nav.ensure_bag()
        run.nav._navigation_click("open_enhancement",1700,1018)
    raw=run.nav.observe("enhance-1920")["text"]
    if raw["confirm"]=="强化" and title_has_name(raw["title"],run.names[TARGET_ID]):
        run.nav._navigation_click("dismiss_material_tooltip",1750,620)
    error=None
    for _ in range(6):
        observation,fields=run.observe_enhancement()
        if observation.material_slots_used:
            if (RUN/"pending.json").exists():raise GuardError("Pending operation requires reconciliation")
            if not title_has_name(fields["title"],run.names[TARGET_ID]) or observation.level!=run.target.level:
                raise GuardError("Unexpected staged target")
            run.nav._navigation_click("close_material_picker",1840,48)
            run.nav.ensure_bag()
            return enter_target(run)
        try:
            if not title_has_name(fields["title"],run.names[TARGET_ID]):
                raise GuardError("Start from the saved target")
            actual=enhancement_result(run.target,observation,fields,run.profile)
            if actual.level!=run.target.level or actual.stats!=run.target.stats:
                raise GuardError("Target changed outside the recorded experiment")
            break
        except GuardError as exc:
            error=exc
            time.sleep(.2)
    else:raise error
    if not title_has_name(fields["title"],run.names[TARGET_ID]):
        raise GuardError("Start from the saved target")
    if actual.level!=run.target.level or actual.stats!=run.target.stats:
        raise GuardError("Target changed outside the recorded experiment")


def main():
    run=Experiment()
    if (RUN/"pending.json").exists():
        pending=json.loads((RUN/'pending.json').read_text(encoding='utf-8'))
        if pending['target_id']!=TARGET_ID:raise GuardError('Pending target mismatch')
        run.serial.pending_id=pending['id']
        after,fields=run.observe_enhancement()
        if not title_has_name(fields['title'],run.names[TARGET_ID]) or (after.material_slots_used!=0 and after.level!=20):
            raise GuardError('Pending confirmation is not resolved; never resend')
        actual=enhancement_result(run.target,after,fields,run.profile)
        finish_receipt(run,pending,after,actual)
    enter_target(run)
    initial,_=run.observe_enhancement()
    if initial.material_slots_used:
        # Discard an unconfirmed selection from an earlier stopped run. It has
        # no trusted provenance, so it is never consumed by the resumed run.
        run.nav._navigation_click("close_material_picker",1840,48)
        run.nav.ensure_bag()
        enter_target(run)
    while True:
        if (ROOT/'runtime/stop.signal').exists():raise GuardError('Stop requested')
        if (RUN/"pending.json").exists(): raise GuardError("Unresolved operation; no repeat")
        decision=evaluate(run.target,run.inventory,run.profile,run.baseline)
        with (RUN/'decisions.jsonl').open('a',encoding='utf-8') as log:
            log.write(json.dumps(decision,ensure_ascii=False)+'\n')
        if decision["action"]!="enhance":
            from .summarize import write_summary
            save(RUN/"decision-final.json",decision)
            print(json.dumps({"stopped":True,"decision":decision},ensure_ascii=False),flush=True)
            return
        before,fields=run.observe_enhancement()
        if before.material_slots_used:raise GuardError("Expected an empty stage selection")
        if fields["material_filter"]!="4星及以下素材":
            if fields["material_filter"]!="3星及以下素材": raise GuardError("Unknown material filter")
            run.nav._navigation_click("open_material_range",1533,768)
            run.nav.observe("enhance-1920")
            run.nav._navigation_click("select_exp_range",1350,959)
            _,fields=run.observe_enhancement()
            if fields["material_filter"]!="4星及以下素材": raise GuardError("Filter not verified")
        if fields["stage_add"]!="阶段放入": raise GuardError("Stage add disabled")
        proof=verify_stage_settings(run)
        run.nav._navigation_click("stage_add",1767,768)
        run.nav.wait_state("enhance-1920",lambda t:bool(re.search(r"消耗[（(][1-9][0-9]*/",t['material_count'])),timeout=4)
        receipt=confirm_visible_stage(run,before,proof)
        print(json.dumps({"level":run.target.level,"probability_before":decision["probability_lower"]},ensure_ascii=False),flush=True)


if __name__=="__main__":
    main()
