"""Strict local GOOD / Mona import. File completeness is not game-scan coverage."""
from collections import Counter
from functools import lru_cache
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import time

from .model import ROLLS
from .sets import SET_LABELS
from .ui_config import MAINS
from .batch import save

DATA=json.loads((Path(__file__).resolve().parents[1]/'data/import-mappings.json').read_text(encoding='utf-8'))
ALIASES={k.casefold():v for k,v in DATA['set_aliases'].items()}|{v:k for k,v in SET_LABELS.items()}
MONA_SLOTS={'flower':'flower','feather':'plume','sand':'sands','cup':'goblet','head':'circlet',
            'plume':'plume','sands':'sands','goblet':'goblet','circlet':'circlet'}
MONA_STATS=dict(zip(('lifeStatic','lifePercentage','attackStatic','attackPercentage','defendStatic','defendPercentage',
                    'critical','criticalDamage','recharge','elementalMastery','cureEffect','physicalBonus',
                    'fireBonus','waterBonus','iceBonus','thunderBonus','windBonus','rockBonus','dendroBonus'),
                   ('hp','hp_','atk','atk_','def','def_','critRate_','critDMG_','enerRech_','eleMas','heal_',
                    'physical_dmg_','pyro_dmg_','hydro_dmg_','cryo_dmg_','electro_dmg_','anemo_dmg_','geo_dmg_','dendro_dmg_')))
MAX_ITEMS=10000


def integer(v,name,low,high):
    if isinstance(v,bool) or not isinstance(v,int) or not low<=v<=high:raise ValueError(f'{name}必须是 {low}–{high} 的整数')
    return v


def numeric(v,name):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or v<=0:raise ValueError(f'{name}必须为有限正数')
    return v


@lru_cache(maxsize=70)
def legal_totals(key,hits):
    tiers=[int(v*100) for v in ROLLS[key]];sums={0};all_sums=set()
    for _ in range(hits):
        sums={a+b for a in sums for b in tiers};all_sums.update(sums)
    return tuple(sorted(all_sums))


def legal_stat(key,value,level,pending):
    from bisect import bisect_left
    shown=round(value,1 if key.endswith('_') else 0)*100
    totals=legal_totals(key,1 if pending else 1+level//4);at=bisect_left(totals,shown)
    tolerance=5.011 if key.endswith('_') else 50.011
    return any(abs(v-shown)<=tolerance for v in totals[max(0,at-1):at+1])


def normalize(item,slot_hint=None):
    if not isinstance(item,dict):raise ValueError('圣遗物必须是对象')
    mona=slot_hint is not None
    rarity=integer(item.get('star' if mona else 'rarity'),'星级',1,5)
    if rarity!=5:return None
    slot=slot_hint if mona else item.get('slotKey')
    if slot not in MAINS:raise ValueError('部位无效')
    if mona and 'position' in item and MONA_SLOTS.get(item['position'])!=slot:raise ValueError('position 与所在部位列表冲突')
    level=integer(item.get('level'),'等级',0,20)
    source_set=item.get('setName' if mona else 'setKey')
    key=ALIASES.get(source_set.casefold()) if isinstance(source_set,str) else None
    if key not in SET_LABELS:raise ValueError('暂不支持的套装：'+str(source_set)[:80])
    if mona:
        if not isinstance(item.get('mainTag'),dict):raise ValueError('mainTag 必须包含主属性名称和数值')
        numeric(item['mainTag'].get('value'),'主属性数值')
    main=MONA_STATS.get(item.get('mainTag',{}).get('name')) if mona else item.get('mainStatKey')
    if main not in MAINS[slot]:raise ValueError('主属性与部位不符')
    stats=item.get('normalTags' if mona else 'substats')
    pending=[] if mona else item.get('unactivatedSubstats',[])
    if not isinstance(stats,list) or not isinstance(pending,list):raise ValueError('副词条必须为数组')
    if len(pending)>1 or (pending and (level>=4 or len(stats)!=3)):raise ValueError('未激活词条与等级／当前词条数冲突')
    if len(stats) not in ((3,4) if level<4 else (4,)) or len(stats)+len(pending)>4:raise ValueError('五星副词条数与等级不符')
    normalized=[];meta={};initials={}
    for i,s in enumerate(stats+pending):
        if not isinstance(s,dict):raise ValueError('副词条格式无效')
        k=MONA_STATS.get(s.get('name')) if mona else s.get('key')
        if k not in ROLLS or k==main:raise ValueError('副词条类型无效或重复主属性')
        value=numeric(s.get('value'),'副词条数值')*(100 if mona and k.endswith('_') else 1)
        is_pending=i>=len(stats)
        if not legal_stat(k,value,level,is_pending):raise ValueError(f'{k}={value:g} 不符合合法档位，请检查数值／百分比单位')
        normalized.append({'key':k,'value':round(value,6),'pending':is_pending})
        if not mona and s.get('initialValue') is not None:
            v=numeric(s['initialValue'],'初始值')
            candidates=[tier for tier in ROLLS[k] if abs(float(tier)-v)<1e-6]
            # GOODScanner roll_solver exports initialValue at display precision;
            # GOODCapture/other exporters may supply the underlying exact tier.
            # Accept a rounded value only when it identifies one legal tier.
            if not candidates and abs(v-round(v,1 if k.endswith('_') else 0))<1e-6:
                tolerance=.0501 if k.endswith('_') else .5001
                candidates=[tier for tier in ROLLS[k] if abs(float(tier)-v)<=tolerance]
            if len(candidates)!=1:raise ValueError('initialValue 不是合法初始档位')
            initials[k]=float(candidates[0])
    if len({s['key'] for s in normalized})!=len(normalized):raise ValueError('副词条类型重复')
    if initials:meta['initial_values']=initials
    if not mona and item.get('totalRolls') is not None:
        rolls=integer(item['totalRolls'],'totalRolls',3,9)
        if rolls not in (3+level//4,4+level//4):raise ValueError('totalRolls 与等级冲突')
        if level==20:meta['upgrade_rolls']=rolls-4
    crafted=item.get('elixirCrafted',item.get('elixerCrafted'))
    if crafted is not None and not isinstance(crafted,bool):raise ValueError('elixirCrafted 必须为布尔值')
    if 'elixirCrafted' in item and 'elixerCrafted' in item and item['elixirCrafted']!=item['elixerCrafted']:raise ValueError('两个定制标记冲突')
    kind='defined' if crafted is True else 'ordinary' if crafted is False else 'unknown'
    field='equip' if mona else 'location'
    if field not in item:
        owner='IMPORT_OWNER_UNKNOWN';ownership_unknown=True
    else:
        owner=item[field]
        if owner is None and mona:owner=''
        if not isinstance(owner,str) or len(owner)>100:raise ValueError('装备归属格式无效')
        owner=owner.strip()
        mapped=DATA['characters'].get(owner)
        if mapped:owner=mapped
        ownership_unknown=bool(owner and not mapped and not any('\u4e00'<=c<='\u9fff' for c in owner))
        if owner and not ownership_unknown:owner+='已装备'
    lock=item.get('lock',False)
    if not isinstance(lock,bool):raise ValueError('lock 必须为布尔值')
    omit=item.get('omit',False) if mona else False
    if not isinstance(omit,bool):raise ValueError('omit 必须为布尔值')
    return {'name':DATA['piece_names'][key][slot],'setKey':key,'slotKey':slot,'mainStatKey':main,'level':level,'rarity':5,
            'substats':normalized,'equip_raw':owner,'lock':lock,'special':crafted is True,'enhancement_kind':kind,
            'excluded':omit,'import_metadata':meta,'ownership_unknown':ownership_unknown}


def parse_document(content):
    if not isinstance(content,str) or len(content.encode('utf-8'))>10*1024*1024:raise ValueError('JSON 文件最大 10 MiB')
    try:document=json.loads(content.lstrip('\ufeff'),parse_constant=lambda s:(_ for _ in ()).throw(ValueError('JSON 中不能包含 NaN/Infinity')))
    except (ValueError,RecursionError) as exc:raise ValueError('无法读取 JSON：'+str(exc)[:150]) from exc
    if not isinstance(document,dict):raise ValueError('需要 GOOD 或莫娜完整导出对象')
    entries=[]
    if document.get('format')=='GOOD':
        version=integer(document.get('version'),'GOOD 版本',1,3)
        if not isinstance(document.get('artifacts'),list):raise ValueError('GOOD 文件没有 artifacts 数组，请导出圣遗物')
        entries=[(f'artifacts[{i}]',row,None) for i,row in enumerate(document['artifacts'])];format_name=f'GOOD v{version}'
    elif any(k in document for k in MONA_SLOTS):
        seen_slots=set()
        for k,slot in MONA_SLOTS.items():
            values=document.get(k,[])
            if not isinstance(values,list):raise ValueError(f'莫娜 {k} 必须为数组')
            if values and slot in seen_slots:raise ValueError('同一部位同时含有两组别名列表，请保留一组，避免重复导入')
            if values:seen_slots.add(slot)
            entries.extend((f'{k}[{i}]',row,slot) for i,row in enumerate(values))
        format_name='莫娜'
    else:raise ValueError('未识别的格式：请选择 GOODScanner / GOOD v1–v3 或莫娜圣遗物 JSON；培养配置应在角色页导入')
    if not 1<=len(entries)<=MAX_ITEMS:raise ValueError('文件需要包含 1–10000 件圣遗物')
    rows=[];errors=[];skipped=0
    for where,item,slot in entries:
        try:row=normalize(item,slot)
        except (ValueError,KeyError,TypeError) as exc:
            errors.append({'item':where,'reason':str(exc)[:200]});continue
        if row is None:skipped+=1;continue
        row['index']=len(rows)+1;rows.append(row)
    from .loadouts import fingerprint
    fps=Counter(fingerprint({'set_key':r['setKey'],'slot':r['slotKey'],'main':r['mainStatKey'],'level':r['level'],
                             'rarity':5,'substats':r['substats']}) for r in rows if len(r['substats'])==4)
    summary={'format':format_name,'total':len(entries),'accepted':len(rows),'skipped_non_five':skipped,'error_count':len(errors),
             'errors':errors[:30],'can_import':bool(rows) and not errors,'unknown_owner':sum(r['ownership_unknown'] for r in rows),
             'unknown_kind':sum(r['enhancement_kind']=='unknown' for r in rows),'defined':sum(r['special'] for r in rows),
             'missing_preview':sum(len(r['substats'])<4 for r in rows),'mature':sum(r['level']==20 for r in rows),
             'excluded':sum(r['excluded'] for r in rows),'identical_extra':sum(n-1 for n in fps.values() if n>1),
             'slots':dict(Counter(r['slotKey'] for r in rows)),
             'scope':'仅代表文件中的五星库存，不声明已经扫描或覆盖游戏全库；重复属性物品保留为不同物品。'}
    return rows,summary


def commit_import(runtime,rows,summary,label):
    if not summary['can_import']:raise ValueError('请先修正导入错误，不会静默丢弃错误的五星圣遗物')
    encoded=json.dumps(rows,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()
    digest=hashlib.sha256(encoded).hexdigest()
    root=Path(runtime)/'imports';root.mkdir(parents=True,exist_ok=True)
    directory=root/('import-'+digest[:24])
    if directory.exists():return directory,True
    # All data is validated before writing. Only normalized artifact fields persist.
    import tempfile,os
    temp=Path(tempfile.mkdtemp(prefix='.pending-',dir=root))
    try:
        save(temp/'enhancer-artifacts.json',rows)
        save(temp/'scan-count.json',{'requested':len(rows),'source':'import','game_scan_verified':False})
        label=Path(str(label).replace('\\','/')).name[:100] or summary['format']+' 导入'
        save(temp/'import-info.json',{'label':label,'created':time.time(),'digest':digest,'summary':summary})
        os.replace(temp,directory)
    except Exception:
        for p in temp.iterdir():p.unlink()
        temp.rmdir();raise
    return directory,False


def imported_metadata(directory):
    path=Path(directory)/'enhancer-artifacts.json'
    return {f'{path.parent.name}:{r["index"]}':r['import_metadata'] for r in json.loads(path.read_text(encoding='utf-8-sig'))
            if r.get('import_metadata')}
