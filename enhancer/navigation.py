"""Deterministic, non-consuming bag navigation through Frostflake; OCR remains yas."""
from datetime import datetime
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import subprocess
import time

from PIL import Image
import numpy as np
import requests
from .yas_client import YasClient
from .recognition import read_verified,label_matches,normalize_ui_labels,set_row_candidate

ROOT = Path(__file__).resolve().parents[1]


def startup_page(text):
    """Known non-consuming return paths; all labels must agree in one frame."""
    if all(label_matches(text.get(k,''),v) for k,v in (('attributes','属性'),('weapon','武器'),('artifact','圣遗物'))):
        return 'character'
    if (label_matches(text.get('compare',''),'对比') and label_matches(text.get('recommend',''),'圣遗物推荐')
            and label_matches(text.get('enhance',''),'强化')
            and any(label_matches(text.get('equip_action',''),v) for v in ('装备','替换','卸下'))):
        return 'equipment_selection'
    return None


def row_major_points(points,tolerance=18):
    rows=[]
    for point in sorted(points,key=lambda p:(p[1],p[0])):
        if not rows or point[1]-rows[-1][0][1]>tolerance:rows.append([])
        rows[-1].append(point)
    return [p for row in rows for p in sorted(row,key=lambda p:p[0])]


class Navigation:
    def __init__(self):
        self.http = requests.Session()
        self.http.trust_env = False
        self.observation = None
        self.directory = None
        self.reader = None

    def yas(self):
        if self.reader is None:
            self.reader = YasClient()
        return self.reader

    def session(self):
        state = json.loads((ROOT / "runtime/session.json").read_text(encoding="utf-8-sig"))
        if state["state"] != "running" or state["busy"]:
            raise RuntimeError("Controller unavailable or scanner busy")
        if state["endpoint"] != "http://127.0.0.1:32333":
            raise RuntimeError("Only the local Frostflake endpoint is allowed")
        return state

    def api(self, method, path):
        if method in ('POST','PATCH') and (ROOT/'runtime/stop.signal').exists():
            raise RuntimeError('Stop requested; no further game input')
        state = self.session()
        response = self.http.request(method, state["endpoint"] + path,
                                     headers={"Origin": "http://127.0.0.1", "Authorization": "Bearer " + state["token"]},
                                     timeout=5)
        response.raise_for_status()
        return response.json() if response.content else None

    def observe(self, layout="bag-1920"):
        started=time.perf_counter()
        directory = ROOT / "runtime" / ("observe-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        directory.mkdir()
        self.observation = self.yas().call("observe", directory, ROOT / f"layouts/{layout}.json")
        self.observation['raw_text']=self.observation['text']
        self.observation['text']=normalize_ui_labels(self.observation['text'])
        self.directory = directory
        self.observed_at = time.monotonic()
        if (self.observation["window"]["width"], self.observation["window"]["height"]) != (1920, 1080):
            raise RuntimeError("Only calibrated 1920x1080 layout supported")
        from .telemetry import record
        record('observe',layout,started)
        return self.observation

    def ensure_bag(self):
        self.wait_state("bag-1920",lambda data: label_matches(data["page"],"圣遗物") and re.search(r"\d+\s*/\s*\d+",data["inventory_count"]))

    def observe_validated(self,layout,validate,**options):
        def record(event):
            event.update(layout=layout,evidence=str(self.directory),at=datetime.now().isoformat())
            with (ROOT/'runtime/recognition.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(event,ensure_ascii=False)+'\n')
        return read_verified(lambda:self.observe(layout)['text'],validate,record=record,**options)

    def wait_state(self, layout, predicate, timeout=8):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            text=self.observe(layout)["text"]
            if predicate(text): return text
            time.sleep(.15)
        raise RuntimeError(f"Expected UI state not reached: {layout}; evidence={self.directory}")

    def read_current(self):
        started=time.perf_counter()
        directory = ROOT / "runtime" / ("read-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f"))
        directory.mkdir()
        records = self.yas().call("read-current", directory)
        self.item_directory = directory
        from .telemetry import record
        record('read','current-artifact',started)
        return records[0]

    def scroll_grid(self, wheel_steps=24):
        self.ensure_bag()
        win=self.observation['window']
        games=[w for w in self.api('GET','/api/windows') if w['title']=='原神' and w['classname']=='UnityWndClass']
        if len(games)!=1:raise RuntimeError('Ambiguous game window')
        self.api('PATCH',f"/api/windows/{games[0]['hWnd']}")
        self.api('POST',f"/api/SetCursorPos?x={win['left']+1100}&y={win['top']+700}")
        time.sleep(.2)
        for _ in range(abs(wheel_steps)):
            self.api('POST',f'/api/mouse_event?dwFlags=2048&dx=0&dy=0&dwData={-120 if wheel_steps>0 else 120}')
            time.sleep(.07)
        time.sleep(.4)
        self.ensure_bag()

    def grid_points(self):
        self.ensure_bag()
        with Image.open(self.directory/'game.png') as image:
            a=np.asarray(image.convert('RGB'))
        points=[]
        for col in range(8):
            x=round(179+146.4*col)
            band=(a[178:957,x-49:x+49].min(axis=2)>180).mean(axis=1)>.65
            start=None
            for y,on in enumerate(list(band)+[False]):
                if on and start is None:start=y
                if not on and start is not None:
                    if 12<=y-start<=38 and start+178-60>=195:
                        points.append((x,round((start+y)/2+178-60)))
                    start=None
        if not points:raise RuntimeError('Artifact grid footers not found')
        return row_major_points(points)

    def acquisition_enabled(self):
        self.ensure_bag()
        with Image.open(self.directory / "game.png") as im:
            r, g, b = im.getpixel((1215, 138))[:3]
        if abs(r - 211) < 15 and abs(g - 188) < 15 and abs(b - 142) < 15:
            return True
        # The off state must be calibrated separately, never inferred from a mismatch.
        return None

    def _navigation_click(self, action, x, y):
        """Private helper: no enhancement confirmation or material buttons here."""
        if action not in {"equip_menu","equip_replace","equip_slot","equip_next","equip_button","equip_confirm","equip_cancel", "filter_option", "acquisition_off", "open_filter", "open_sort", "sort_quality", "close_menu", "scroll_top", "reset_filter", "apply_filter", "close_character", "close_equipment_selection", "open_set_filter", "select_target_set", "apply_set_filter", "select_grid_item", "clear_set_filter", "open_enhancement", "open_material_picker", "inspect_exp_stack", "close_material_picker", "dismiss_material_tooltip", "open_material_range", "select_exp_range", "stage_add", "open_material_settings", "close_material_settings"}:
            raise RuntimeError("Not a permitted navigation action")
        if not self.observation or time.monotonic() - self.observed_at > 5:
            raise RuntimeError("Stale navigation observation")
        games = [w for w in self.api("GET", "/api/windows") if w["title"] == "原神" and w["classname"] == "UnityWndClass"]
        if len(games) != 1:
            raise RuntimeError("Ambiguous game window")
        window = self.observation["window"]
        game = games[0]
        fast=action=='select_grid_item'
        if not (game["x"] <= window["left"] < game["x"] + game["width"] and
                game["y"] <= window["top"] < game["y"] + game["height"]):
            raise RuntimeError("Game moved since observation")
        self.api("PATCH", f'/api/windows/{game["hWnd"]}')
        time.sleep(.04 if fast else .15)
        self.api("POST", f'/api/SetCursorPos?x={window["left"] + x}&y={window["top"] + y}')
        time.sleep(.02 if fast else .06)
        self.api("POST", "/api/mouse_event?dwFlags=2&dx=0&dy=0&dwData=0")
        time.sleep(.04 if fast else .08)
        self.api("POST", "/api/mouse_event?dwFlags=4&dx=0&dy=0&dwData=0")
        with (ROOT / "runtime/navigation.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps({"action": action, "at": datetime.now().isoformat(), "evidence": str(self.directory)}) + "\n")
        time.sleep(.12 if fast else .5)

    def turn_off_acquisition_order(self):
        if self.acquisition_enabled() is not True:
            raise RuntimeError("Acquisition toggle not positively recognized as enabled")
        self._navigation_click("acquisition_off", 1240, 138)
        return self.observe()

    def clear_open_filter(self):
        text = self.observe("filter-1920")["text"]
        if text != {"heading": "圣遗物筛选", "reset": "重置", "apply": "确认"}:
            raise RuntimeError("Filter dialog not recognized")
        self._navigation_click("reset_filter", 165, 1020)
        text = self.observe("filter-1920")["text"]
        if text["heading"] != "圣遗物筛选" or text["apply"] != "确认":
            raise RuntimeError("Filter dialog changed")
        self._navigation_click("apply_filter", 480, 1020)
        self.ensure_bag()

    def return_from_character_to_bag(self):
        text=self.observe('startup-1920')['text']
        page=startup_page(text)
        if page=='equipment_selection':
            self._navigation_click('close_equipment_selection',1840,49)
            self.wait_state('startup-1920',lambda t:startup_page(t)=='character')
        elif page!='character':
            raise RuntimeError('无法识别扫描入口：支持背包圣遗物页、角色属性/圣遗物页或圣遗物穿戴列表；未执行盲目返回。')
        text = self.observe("character-1920")["text"]
        text = {k: v.strip(" 。`'\"·") for k, v in text.items()}
        if text != {"attributes": "属性", "weapon": "武器", "artifact": "圣遗物"}:
            raise RuntimeError("Character screen not recognized")
        self._navigation_click("close_character", 1840, 49)
        time.sleep(.8)
        # B (Windows scan code 0x30): opens the bag; never types confirmation keys.
        self.api("POST", "/api/keybd_event?bVk=66&bScan=48&dwFlags=0&dwExtraInfo=0")
        time.sleep(.08)
        self.api("POST", "/api/keybd_event?bVk=66&bScan=48&dwFlags=2&dwExtraInfo=0")
        time.sleep(1)
        self.ensure_bag()

    def select_open_set(self, set_name, *, target=None):
        text = self.wait_state("sets-1920",lambda t:"圣遗物套装筛选" in t["heading"] and t["apply"]=="确认筛选")
        self._navigation_click("clear_set_filter", 148, 1018)
        text = self.observe("sets-1920")["text"]
        matches = [key for key, value in text.items() if key.startswith(("left_", "right_")) and label_matches(value,set_name)]
        if not matches:
            ranked = sorted(((SequenceMatcher(None, value, set_name).ratio(), key)
                             for key, value in text.items() if key.startswith(("left_", "right_"))), reverse=True)
            if ranked[0][0] >= .6 and ranked[0][0] - ranked[1][0] >= .2:
                matches = [ranked[0][1]]
        if len(matches) == 1:
            side, row = matches[0].split("_")
            self._navigation_click("select_target_set", 77 if side == "left" else 728, 154 + int(row) * 82)
        else:
            self.find_scrolled_set(set_name)
        text = self.wait_state("sets-1920",lambda t:label_matches(t["apply"],"确认筛选") and label_matches(t["selected_set"],set_name))
        self._navigation_click("apply_set_filter", 1717, 1018)
        self.wait_state("filter-1920",lambda t:t["heading"]=="圣遗物筛选")
        if target is not None:
            options=self.observe("filter-options-1920")["text"]
            if options!={"locked":"仅锁定","nonmax":"未满级","idle":"未装备"}:
                raise RuntimeError(f"Filter options not recognized: {options}")
            if target.locked:
                self._navigation_click("filter_option",445,473)
            self.observe("filter-options-1920")
            self._navigation_click("filter_option",445,669)
            if not target.equipped or target.equipped.startswith("UNKNOWN:"):
                self.observe("filter-options-1920")
                self._navigation_click("filter_option",445,794)
            self.observe("filter-1920")
        self._navigation_click("apply_filter", 478, 1018)
        self.ensure_bag()

    def find_scrolled_set(self,set_name):
        last=None
        for page in range(12):
            text=self.observe('sets-search-1920')['text']
            matching=[key for key,value in text.items() if label_matches(value,set_name)]
            if matching:
                side,y=matching[len(matching)//2].split('_')
                self._navigation_click('select_target_set',77 if side=='left' else 728,int(y)+22)
                return
            candidate=set_row_candidate(text,set_name)
            if candidate:
                side,y=candidate
                self._navigation_click('select_target_set',77 if side=='left' else 728,y)
                # A probable list match is never enough to apply a filter.
                self.wait_state('sets-1920',lambda t:label_matches(t['selected_set'],set_name))
                return
            signature=tuple(text.values())
            if signature==last:break
            last=signature
            win=self.observation['window']
            self.api('POST',f"/api/SetCursorPos?x={win['left']+1050}&y={win['top']+650}")
            for _ in range(10):
                self.api('POST','/api/mouse_event?dwFlags=2048&dx=0&dy=0&dwData=-120')
                time.sleep(.07)
            time.sleep(.35)
        raise RuntimeError(f'Set not found while scrolling: {set_name}')


if __name__ == "__main__":
    nav = Navigation()
    print(json.dumps(nav.turn_off_acquisition_order(), ensure_ascii=False))
    print(nav.directory)
