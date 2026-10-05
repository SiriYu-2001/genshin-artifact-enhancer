"""Saved five-piece loadouts: yas OCR + Frostflake input, with live verification.

UI geometry informed by GOODScanner (Anyrainel, 4e4bc882), independently
implemented around this project's existing reader and controller. No enhancement,
unlocking, fodder or deletion controls are used here.
"""
from datetime import datetime
from difflib import SequenceMatcher
import json
from pathlib import Path
import re
import time

import numpy as np
from PIL import Image

from .navigation import Navigation,ROOT
from .batch import save
from .loadouts import fingerprint,load_library,plan_file
from .model import SLOTS
from .sets import SET_LABELS
from .text_identity import canonical
from .recognition import label_edges,label_matches,read_verified,ReadRejected

LABELS={'生命值':'hp','攻击力':'atk','防御力':'def','元素精通':'eleMas',
        '元素充能效率':'enerRech_','暴击率':'critRate_','暴击伤害':'critDMG_',
        '火元素伤害加成':'pyro_dmg_','水元素伤害加成':'hydro_dmg_','冰元素伤害加成':'cryo_dmg_',
        '雷元素伤害加成':'electro_dmg_','风元素伤害加成':'anemo_dmg_','岩元素伤害加成':'geo_dmg_',
        '草元素伤害加成':'dendro_dmg_','物理伤害加成':'physical_dmg_','治疗加成':'heal_'}
TABS=dict(zip(SLOTS,[80,200,325,425,525]))
DECOR='·•・●`、:：.．。,，-'


def clean(text):
    return canonical(text).translate(str.maketrans({'％':'%','＋':'+','，':','}))


def borrow_dialog(text):
    body=clean(''.join(v for k,v in text.items() if k.startswith('body')))
    return (label_matches(text.get('confirm',''),'确认') and
            label_matches(text.get('cancel',''),'取消') and
            ('装备' in body or '替换' in body) and ('更换' in body or '替换' in body))


def loadout_allows_borrow(entry):
    policy=entry.get('equipment_policy')
    if policy is None:
        source=entry.get('source_allocation')
        if source and Path(source).is_file():
            policy=json.loads(Path(source).read_text(encoding='utf-8-sig')).get('equipment')
    if policy is None:return False
    if policy not in ('borrow','protected'):raise ValueError('Invalid saved equipment policy')
    return policy=='borrow'


def quick_reject(text,attributes):
    raw=clean(text.get('level','')).strip(DECOR)
    if re.fullmatch(r'\+(?:[0-9]|1[0-9]|20)',raw) and int(raw[1:])!=attributes['level']:return True
    label=clean(text.get('main','')).strip(DECOR)
    if label in LABELS:
        key=LABELS[label]
        if key in ('atk','def','hp') and not ((attributes['slot']=='flower' and key=='hp') or (attributes['slot']=='plume' and key=='atk')):key+='_' 
        if key!=attributes['main']:return True
    return False


def parse_panel(text,slot,rarity):
    level=clean(text['level']).strip(DECOR)
    if not re.fullmatch(r'\+?(?:[0-9]|1[0-9]|20)',level):raise ValueError('Unreadable equipment level')
    label=clean(text['main']).strip(DECOR)
    if label not in LABELS:raise ValueError('Unreadable equipment main stat')
    main=LABELS[label]
    # Flat HP/ATK main stats exist only on flower/plume. Other main stats
    # with these labels are percentages; value is fixed by rarity and level.
    if main in ('atk','def','hp') and not ((slot=='flower' and main=='hp') or (slot=='plume' and main=='atk')):
        main+='_' 
    subs=[]
    for i in range(4):
        value=clean(text[f'sub_{i}']).strip(DECOR).removesuffix('↑')
        value=re.sub(r'(?<=\d)\.(?=%$)','',value)
        m=re.fullmatch(r'(生命值|攻击力|防御力|元素精通|元素充能效率|暴击率|暴击伤害)\+?(\d+(?:\.\d+)?)(%?)',value)
        if not m:raise ValueError(f'Unreadable equipment substat {i}: {value}')
        key=LABELS[m[1]]
        if key in ('hp','atk','def') and m[3]:key+='_' 
        if key.endswith('_')!=bool(m[3]):raise ValueError('Substat unit mismatch')
        subs.append({'key':key,'value':float(m[2]),'pending':False})
    if len({s['key'] for s in subs})!=4:raise ValueError('Duplicate substats')
    set_label=re.split(r'[:：(（]',clean(text['set']),maxsplit=1)[0].strip(DECOR)
    sets=[k for k,v in SET_LABELS.items() if v==set_label]
    if len(sets)!=1:raise ValueError(f'Unreadable equipment set: {set_label}')
    return {'set_key':sets[0],'slot':slot,'rarity':rarity,'main':main,'level':int(level.lstrip('+')),'substats':subs}


class EquipDriver:
    def __init__(self,directory):
        self.nav=Navigation();self.directory=Path(directory)
        self.filter=None;self.cache={};self.slot=None;self.aliases=[]
        self.allow_borrow=False

    def observe(self,layout='equip-selection-1920'):
        if (ROOT/'runtime/stop.signal').exists():raise RuntimeError('Stop requested')
        return self.nav.observe(layout)['text']

    def focus(self):
        games=[w for w in self.nav.api('GET','/api/windows') if w['title']=='原神' and w['classname']=='UnityWndClass']
        if len(games)!=1:raise RuntimeError('Game window is not unique')
        self.nav.api('PATCH',f"/api/windows/{games[0]['hWnd']}")

    def key(self,key):
        vk,scan={'escape':(27,1),'character':(67,46)}[key]
        self.focus()
        self.nav.api('POST',f'/api/keybd_event?bVk={vk}&bScan={scan}&dwFlags=0&dwExtraInfo=0')
        time.sleep(.07)
        self.nav.api('POST',f'/api/keybd_event?bVk={vk}&bScan={scan}&dwFlags=2&dwExtraInfo=0')
        time.sleep(.65)

    def click(self,action,x,y):
        if time.monotonic()-self.nav.observed_at>4:self.observe()
        self.nav._navigation_click(action,x,y)

    def dismiss_borrow_dialog(self):
        text=self.observe('equip-confirm-1920')
        if not borrow_dialog(text):return None
        before=str(self.nav.directory)
        self.click('equip_cancel',760,755)
        def closed(text):
            if label_matches(text.get('confirm',''),'确认') and label_matches(text.get('cancel',''),'取消'):
                raise ReadRejected('Borrow dialog has not closed')
            return text
        read_verified(lambda:self.observe('equip-confirm-1920'),closed,attempts=4)
        return {'before':before,'after':str(self.nav.directory)}

    def character_name(self,text):
        title=label_edges(clean(text.get('character','')))
        # The slash can read as !; background particles can read as °.
        # Keep letters and digits intact so OCR never silently changes a name.
        match=re.fullmatch(r'(?:火|水|冰|雷|风|岩|草)元素[/／1I丨|!！]?(.*)',title)
        return label_edges(match[1]) if match and match[1] else None

    def enter(self,aliases):
        self.aliases=[clean(x) for x in aliases]
        self.focus();time.sleep(.4)
        text=self.observe('equip-character-1920')
        if not self.character_name(text):
            # Return from an already open artifact/filter submenu before C.
            selection=self.observe()
            sets=self.observe('sets-1920')
            if '圣遗物套装筛选' in sets['heading']:
                self.click('close_character',1840,48)
                self.key('escape')
                text=self.observe('equip-character-1920')
            elif clean(selection['action']) in ('替换','装备','卸下'):
                self.key('escape');text=self.observe('equip-character-1920')
        if not self.character_name(text):
            for _ in range(3):
                self.key('character');text=self.observe('equip-character-1920')
                if self.character_name(text):break
                self.key('escape')
            else:raise RuntimeError('Cannot open character screen')
        seen=set();first_name=None;previous_name=None;recoveries=0
        for _ in range(150):
            name=self.character_name(text)
            if name is None:
                # Do not keep clicking the next-character coordinate after
                # leaving the character page or during an unreadable transition.
                for _ in range(3):
                    time.sleep(.15);text=self.observe('equip-character-1920')
                    name=self.character_name(text)
                    if name:break
                if name is None:
                    if recoveries>=2:raise RuntimeError('Character page lost during selection')
                    recoveries+=1
                    self.key('character');text=self.observe('equip-character-1920')
                    name=self.character_name(text)
                    if not name:raise RuntimeError('Cannot restore character page during selection')
            if name not in self.aliases and any(a in clean(text.get('character','')) or
                    # A single wrong glyph in a three-glyph name scores 2/3.
                    # Similarity only requests another frame; identity remains exact.
                    (name and SequenceMatcher(None,name,a).ratio()>=.6) for a in self.aliases):
                for _ in range(3):
                    text=self.observe('equip-character-1920');name=self.character_name(text)
                    if name in self.aliases:break
            if name in self.aliases:break
            if first_name is None:first_name=name
            if name==first_name and previous_name not in (None,name) and len(seen)>1:
                save(self.directory/'character-search.json',{'aliases':self.aliases,'observed':sorted(seen),'last_evidence':str(self.nav.directory)})
                raise RuntimeError('Target character not found: '+', '.join(self.aliases))
            if name:seen.add(name)
            previous_name=name
            self.click('equip_next',1850,530)
            text=self.observe('equip-character-1920')
        else:raise RuntimeError('Target character not found')
        save(self.directory/'character.json',{'name':name,'evidence':str(self.nav.directory)})
        if label_edges(clean(text['artifact_menu']))!='圣遗物':raise RuntimeError('Artifact menu unreadable')
        self.click('equip_menu',160,293)
        text=self.observe('equip-character-1920')
        if label_edges(clean(text['replace']))!='替换':raise RuntimeError(f'Equipment selection entrance unreadable: {text}')
        self.click('equip_replace',1720,1010)
        self.observe()

    def choose_set(self,key):
        if self.filter==key:return
        self.observe();self.click('open_set_filter',309,1008)
        text=self.nav.wait_state('sets-1920',lambda t:'圣遗物套装筛选' in t['heading'] and label_matches(t['apply'],'确认筛选'))
        if label_matches(text['selected_set'],SET_LABELS[key]):
            self.click('apply_set_filter',1717,1018);self.observe();self.filter=key;return
        win=self.nav.observation['window']
        self.nav.api('POST',f"/api/SetCursorPos?x={win['left']+1050}&y={win['top']+650}")
        for _ in range(100):
            self.nav.api('POST','/api/mouse_event?dwFlags=2048&dx=0&dy=0&dwData=120')
            time.sleep(.015)
        self.observe('sets-1920')
        self.click('clear_set_filter',148,1018)
        text=self.observe('sets-1920');label=SET_LABELS[key]
        matches=[k for k,v in text.items() if k.startswith(('left_','right_')) and label_matches(v,label)]
        if not matches:
            # List labels can lose characters at their small font size. This
            # only picks a filter candidate: the large selected title below
            # must still match exactly before the filter can be applied.
            ranked=sorted((SequenceMatcher(None,clean(v),label).ratio(),k)
                          for k,v in text.items() if k.startswith(('left_','right_')))
            if len(ranked)>1 and ranked[-1][0]>=.75 and ranked[-1][0]-ranked[-2][0]>=.2:
                matches=[ranked[-1][1]]
        if len(matches)==1:
            side,row=matches[0].split('_')
            self.click('select_target_set',77 if side=='left' else 728,154+int(row)*82)
        else:self.nav.find_scrolled_set(label)
        self.nav.wait_state('sets-1920',lambda t:label_matches(t['selected_set'],label) and label_matches(t['apply'],'确认筛选'))
        self.click('apply_set_filter',1717,1018)
        self.observe();self.filter=key

    def select_slot(self,slot):
        self.observe();self.click('equip_slot',TABS[slot],28)
        self.slot=slot;self.observe()

    def read(self):
        text=self.observe()
        with Image.open(self.nav.directory/'game.png') as im:
            # Fixed star centers from the selection panel, verified per frame.
            rgb=np.asarray(im.convert('RGB'))
            def gold(x):
                c=np.median(rgb[278:283,x-2:x+3],axis=(0,1))
                return c[0]>180 and c[1]>120 and c[0]>c[2]+45
            if not gold(1611):raise ValueError('Target five-star icon unreadable')
        return parse_panel(text,self.slot,5),text

    def matches(self,ref,retries=1,probe=False):
        if probe and quick_reject(self.observe('equip-probe-1920'),ref['attributes']):return None
        for _ in range(max(4,retries)):
            try:
                actual,text=self.read()
                if fingerprint(actual)==ref['fingerprint']:return actual,text
                expected=ref['attributes']
                if all(actual[k]==expected[k] for k in ('set_key','main','level')):
                    wanted={(s['key'],s['value']) for s in expected['substats']}
                    found={(s['key'],s['value']) for s in actual['substats']}
                    if len(wanted & found)>=3:
                        time.sleep(.08);continue
                return None
            except ValueError:
                raw=clean(self.nav.observation['text'].get('level','')).strip('·`、.。:：').lstrip('+')
                if raw.isdigit() and int(raw)!=ref['attributes']['level']:return None
                time.sleep(.08)
        return None

    def scrollbar_position(self):
        with Image.open(self.nav.directory/'game.png') as im:
            rgb=np.asarray(im.convert('RGB'))
        colors=np.median(rgb[115:949,601:607],axis=1)
        mask=(colors[:,0]>120)&(colors[:,1]>140)&(colors[:,2]>150)&(np.ptp(colors,axis=1)<65)
        ranges=[];start=None
        for i,on in enumerate(list(mask)+[False]):
            if on and start is None:start=i
            if not on and start is not None:
                if i-start>15:ranges.append((start,i))
                start=None
        return max(ranges,key=lambda r:r[1]-r[0]) if ranges else None

    def scroll(self,steps):
        before=self.scrollbar_position()
        self.focus();win=self.nav.observation['window']
        self.nav.api('POST',f"/api/SetCursorPos?x={win['left']+550}&y={win['top']+650}")
        for _ in range(abs(steps)):
            self.nav.api('POST',f'/api/mouse_event?dwFlags=2048&dx=0&dy=0&dwData={-120 if steps>0 else 120}')
            time.sleep(.025)
        time.sleep(.35);self.observe()
        after=self.scrollbar_position()
        return before is None or after is None or max(abs(a-b) for a,b in zip(before,after))>3

    def find(self,ref):
        attr=ref['attributes'];self.select_slot(attr['slot'])
        match=self.matches(ref,3)
        if match:return match
        # The equipped item is pinned at the front in this interface. Treat
        # that position only as a candidate and recheck the complete identity.
        # This avoids reopening filters merely to verify an item just equipped.
        self.scroll(-70)
        self.click('select_grid_item',89,130)
        match=self.matches(ref,3)
        if match:return match
        self.choose_set(attr['set_key']);self.select_slot(attr['slot'])
        match=self.matches(ref,3)
        if match:return match
        cached=self.cache.get(ref['fingerprint'])
        if cached:
            page,row,col=cached
            self.scroll(-max(70,(page+1)*40))
            for _ in range(page):self.scroll(40)
            self.click('select_grid_item',89+141*col,130+167*row)
            match=self.matches(ref,3)
            if match:return match
        back=70
        for probe in (True,False):
            self.scroll(-back);back=70;last=None
            for page in range(25):
                signatures=[]
                for row in range(5):
                    for col in range(4):
                        self.click('select_grid_item',89+141*col,130+167*row)
                        match=self.matches(ref,2,probe=probe)
                        if match:
                            self.cache[ref['fingerprint']]=(page,row,col)
                            print(json.dumps({'found':ref['name'],'page':page,'cell':[row,col],'fast_probe':probe},ensure_ascii=False),flush=True)
                            return match
                        signatures.append(tuple(self.nav.observation['text'].values()))
                signature=tuple(signatures)
                if signature==last:break
                last=signature
                if not self.scroll(40):break
                back+=40
            if probe:print(json.dumps({'locate_fallback':'full-attribute pass','target':ref['name']},ensure_ascii=False),flush=True)
        raise RuntimeError(f"Saved artifact not found: {ref['name']}")

    def owned(self,text):
        owner=label_edges(clean(text['owner']))
        return any(owner==a+'已装备' for a in self.aliases) and label_edges(clean(text['action']))=='卸下'

    def confirm_owned(self,ref,match):
        if match and self.owned(match[1]):return match
        def validate(observed):
            if not observed or not self.owned(observed[1]):
                raise ReadRejected('Complete identity and equipment owner not confirmed')
            return observed
        try:return read_verified(lambda:self.matches(ref,2),validate,attempts=5)
        except ReadRejected:return None

    def read_actionable(self,ref,match):
        def validate(observed):
            if not observed or label_edges(clean(observed[1]['action'])) not in ('装备','替换','卸下'):
                raise ReadRejected('Equipment action not readable on the verified item')
            return observed
        try:return validate(match)
        except ReadRejected:
            return read_verified(lambda:self.matches(ref,2),validate,attempts=4)

    def equip(self,ref):
        actual,text=self.read_actionable(ref,self.find(ref))
        if self.owned(text):return {'status':'already_correct','attributes':actual,'evidence':str(self.nav.directory)}
        action=clean(text['action']).strip(DECOR)
        if action=='卸下':
            match=self.confirm_owned(ref,(actual,text))
            if not match:raise RuntimeError('Equipped artifact owner could not be confirmed')
            return {'status':'already_correct','attributes':match[0],'evidence':str(self.nav.directory)}
        if action not in ('装备','替换'):raise RuntimeError(f'Unexpected equipment action: {text["action"]}')
        operation={'target':ref,'before_owner':text['owner'],'before_evidence':str(self.nav.directory),
                   'target_character_aliases':self.aliases}
        save(self.directory/'equip-pending.json',operation)
        self.click('equip_button',1558,1025)
        dialog=self.observe('equip-confirm-1920')
        if label_matches(dialog['confirm'],'确认') or label_matches(dialog['cancel'],'取消'):
            def verified_dialog(text):
                if not borrow_dialog(text):raise ReadRejected('Borrow confirmation not recognized')
                return text
            try:verified_dialog(dialog)
            except ReadRejected:dialog=read_verified(lambda:self.observe('equip-confirm-1920'),verified_dialog,attempts=4)
            if not self.allow_borrow:
                proof=self.dismiss_borrow_dialog()
                if proof:
                    save(self.directory/'equip-cancelled.json',dict(operation,reason='borrowing_disabled',cancellation=proof))
                    (self.directory/'equip-pending.json').unlink()
                raise RuntimeError('Borrowing is disabled for this saved loadout')
            # When borrowing is authorized, the donor's spelling is informational.
            # Full item identity was verified before this click and is rechecked
            # with the destination owner after the operation.
            operation['borrow_confirmation']=dict(dialog)
            save(self.directory/'equip-pending.json',operation)
            self.click('equip_confirm',1178,755)
        for _ in range(8):
            match=self.matches(ref,2)
            if match and self.owned(match[1]):
                result={'status':'equipped','attributes':match[0],'owner':match[1]['owner'],
                        'evidence':str(self.nav.directory),'before_owner':operation['before_owner']}
                save(self.directory/f"equipped-{ref['attributes']['slot']}.json",result)
                (self.directory/'equip-pending.json').unlink()
                return result
            time.sleep(.3)
        raise RuntimeError('Equipment result not confirmed; no repeated click')


def apply(library_path,ids,scan,updates=None,policy='strict',*,full_audit=False):
    from .good_backend import apply_loadouts
    return apply_loadouts(library_path,ids,scan,updates,policy,full_audit=full_audit)


def legacy_apply(library_path,ids,scan,updates=None,policy='strict'):
    plan=plan_file(library_path,ids,scan,updates,policy)
    if plan['status']=='blocked':raise RuntimeError('Saved loadout plan has conflicts or missing artifacts')
    from .__main__ import ensure_controller
    ensure_controller()
    library=load_library(library_path)
    policies={row['id']:loadout_allows_borrow(library['loadouts'][row['id']][-1])
              for row in plan['loadouts'] if row['status']=='ready'}
    previous_path=ROOT/'runtime/active-equip.json'
    previous=json.loads(previous_path.read_text(encoding='utf-8')) if previous_path.exists() else {}
    pending_path=Path(previous['directory'])/'equip-pending.json' if previous.get('directory') else None
    directory=ROOT/'runtime'/datetime.now().strftime('equip-%Y%m%d-%H%M%S')
    directory.mkdir();save(directory/'plan.json',plan)
    result={'status':'running','loadouts':[],'directory':str(directory),'inventory_source':str(scan)}
    save(ROOT/'runtime/active-equip.json',result)
    driver=EquipDriver(directory)
    try:
        recovered=driver.dismiss_borrow_dialog()
        if recovered:
            save(directory/'entry-recovery.json',recovered)
            if pending_path and pending_path.exists():
                old=json.loads(pending_path.read_text(encoding='utf-8'))
                save(pending_path.with_name('equip-cancelled.json'),dict(old,cancellation=recovered))
                pending_path.unlink()
        for row in plan['loadouts']:
            if row['status']!='ready':continue
            entry=library['loadouts'][row['id']][-1]
            driver.allow_borrow=policies[row['id']]
            driver.enter(entry.get('character_aliases',[entry['character']]))
            refs=sorted(entry['items'],key=lambda x:SLOTS.index(x['attributes']['slot']))
            # Verify all five specific items exist before the first equipment click.
            preflight=[]
            for ref in refs:
                actual,text=driver.find(ref)
                preflight.append({'name':ref['name'],'attributes':actual,'owner':text['owner'],'evidence':str(driver.nav.directory)})
                print(json.dumps({'preflight':ref['name'],'slot':ref['attributes']['slot'],'owner':text['owner']},ensure_ascii=False),flush=True)
            save(directory/f"preflight-{row['id']}.json",preflight)
            applied=[]
            for ref in refs:
                record=driver.equip(ref);record['slot']=ref['attributes']['slot'];record['name']=ref['name']
                applied.append(record)
                print(json.dumps({'loadout':row['id'],'slot':record['slot'],'status':record['status']},ensure_ascii=False),flush=True)
            # Revisit every slot, checking identity AND owner after the whole set.
            final=[]
            for ref in refs:
                actual,text=driver.find(ref)
                match=driver.confirm_owned(ref,(actual,text))
                if not match:raise RuntimeError('Final equipment verification failed')
                actual,text=match
                final.append({'slot':ref['attributes']['slot'],'fingerprint':fingerprint(actual),'owner':text['owner'],'evidence':str(driver.nav.directory)})
            result['loadouts'].append({'id':row['id'],'character':entry['character'],'items':applied,'verified':final})
            save(directory/'result.json',result);save(ROOT/'runtime/active-equip.json',result)
            driver.key('escape')
            def verify_character(title):
                if driver.character_name(title) not in driver.aliases:raise ReadRejected('Final character mismatch')
                return title
            read_verified(lambda:driver.observe('equip-character-1920'),verify_character,attempts=4)
            driver.filter=None
        result['status']='verified'
    except Exception as e:
        result['status']='needs-attention';result['error']=str(e)
        raise
    finally:
        save(directory/'result.json',result);save(ROOT/'runtime/active-equip.json',result)
        driver.nav.yas().close()
    print(json.dumps(result,ensure_ascii=False),flush=True)
    return result
