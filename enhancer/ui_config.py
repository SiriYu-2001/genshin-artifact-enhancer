"""Validated browser configurations compiled into the existing CLI formats."""
from copy import deepcopy
import json
import math
from pathlib import Path
import re

from .batch import save
from .model import MEANS
from .sets import SET_LABELS

MAINS={'flower':['hp'],'plume':['atk'],'sands':['atk_','hp_','def_','eleMas','enerRech_'],
       'goblet':['atk_','hp_','def_','eleMas','physical_dmg_','pyro_dmg_','hydro_dmg_','cryo_dmg_','electro_dmg_','anemo_dmg_','geo_dmg_','dendro_dmg_'],
       'circlet':['critRate_','critDMG_','atk_','hp_','def_','eleMas','heal_']}


def number(value,name,low,high):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
        raise ValueError(f'{name}必须在 {low}–{high} 之间')
    return value


def validate(data):
    d=deepcopy(data)
    from .elixir_report import settings
    d['elixir']=settings(d.get('elixir'))
    from .dust_report import settings as dust_settings
    d['dust']=dust_settings(d.get('dust'))
    if d.get('mode') not in ('single','multi'):raise ValueError('请选择单角色或多需求模式')
    if not isinstance(d.get('name'),str) or not d['name'].strip() or len(d['name'])>80:raise ValueError('配置名称不能为空，最多80字')
    if d.get('equipment') not in ('borrow','protected'):raise ValueError('装备借用策略无效')
    if d.get('allocation') not in ('priority','independent'):raise ValueError('分配策略无效')
    if not isinstance(d.get('demands'),list) or not 1<=len(d['demands'])<=40:raise ValueError('需要1–40个需求')
    if d['mode']=='single' and len(d['demands'])!=1:raise ValueError('单角色模式只接受一个需求')
    ids=[]
    for entry in d['demands']:
        identifier=entry.get('id','')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',identifier):raise ValueError('需求ID无效')
        ids.append(identifier)
        p=entry.get('profile',{})
        for key in ('character','name'):
            if not isinstance(p.get(key),str) or not p[key].strip() or len(p[key])>100:raise ValueError('角色和用途名称不能为空')
        if p.get('set_key') not in SET_LABELS:raise ValueError('请选择已支持的套装')
        if p.get('set_requirement',4)!=4:raise ValueError('当前支持4+1套装，2+2尚未实现')
        for slot,options in MAINS.items():
            values=p.get('main_stats',{}).get(slot)
            if not isinstance(values,list) or not values or any(v not in options for v in values):raise ValueError(f'{slot} 主属性无效或未选择')
        weights=p.get('weights',{})
        if not isinstance(weights,dict) or set(weights)-set(MEANS):raise ValueError('副属性权重字段无效')
        for key,value in weights.items():number(value,key,0,100)
        number(p.get('threshold'),'概率阈值',0,1)
        number(p.get('artifact_crit_rate_cap'),'暴击率计分上限',0,200)
        number(p.setdefault('artifact_energy_recharge_min',0),'圣遗物额外充能下限',0,300)
        if p.get('resource_target_score') is not None:number(p['resource_target_score'],'资源规划目标分数',0,1000)
        aliases=p.get('character_aliases',[])
        if not isinstance(aliases,list) or any(not isinstance(a,str) or not a.strip() or len(a)>50 for a in aliases):raise ValueError('角色别名无效')
        p['character_aliases']=list(dict.fromkeys([p['character'],*aliases]))
        if p.get('reserved_ids'):raise ValueError('可视化配置暂不支持跨扫描保留ID；请使用原命令行配置，避免保留规则失效')
        p.update(set_label=SET_LABELS[p['set_key']],set_requirement=4,normalization='mean_roll',
                 target_rarity=5,baseline_level=20,reserved_ids=[],auto_equip=False,
                 allowed_equipped_characters=['*'] if d['equipment']=='borrow' else p['character_aliases'])
        entry['profile']=p
    if len(ids)!=len(set(ids)):raise ValueError('需求ID重复')
    scenes=d.get('scenarios')
    if scenes is not None:
        if not isinstance(scenes,list) or not scenes:raise ValueError('至少需要一个场景')
        covered=set()
        for scene in scenes:
            if not isinstance(scene.get('name'),str) or not scene['name'].strip():raise ValueError('场景名称不能为空')
            values=scene.get('demands')
            if not isinstance(values,list) or not values or set(values)-set(ids) or len(values)!=len(set(values)):raise ValueError('场景需求选择无效')
            covered.update(values)
        if covered!=set(ids):raise ValueError('每个需求都必须至少出现在一个场景中')
    if not isinstance(d.get('auto_equip_after',False),bool):raise ValueError('完成后穿戴选项必须是开关')
    if d.get('auto_equip_after') and scenes and len(scenes)>1:
        scene=d.get('equip_scene')
        if isinstance(scene,bool) or not isinstance(scene,int) or not 0<=scene<len(scenes):raise ValueError('请选择完成后要穿戴的场景')
    return d


def compile_config(data,directory):
    d=validate(data);directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    entries=[]
    for entry in d['demands']:
        filename=entry['id']+'.json';save(directory/filename,entry['profile'])
        entries.append({'id':entry['id'],'profile':filename})
    save(directory/'campaign.json',{'version':1,'equipment':d['equipment'],'allocation':d['allocation'],
                                   'demands':entries,'scenarios':d.get('scenarios'),'reserved_ids':[]})
    save(directory/'config.json',d)
    return d


def materialize_snapshot(scan,updates,output):
    """Reuse the same five-star inventory, including confirmed enhancement updates."""
    from .report import load_scan
    from .batch import apply_updates
    scan=Path(scan);output=Path(output)
    inventory,names,coverage=load_scan(scan)
    if not coverage['complete']:raise ValueError('扫描覆盖不完整')
    changes=json.loads(Path(updates).read_text(encoding='utf-8-sig')) if updates else {}
    if set(changes)-{a.id for a in inventory}:raise ValueError('库存更新不属于该扫描')
    latest={a.id:a for a in apply_updates(inventory,changes)}
    raw=json.loads((scan/'enhancer-artifacts.json').read_text(encoding='utf-8-sig'))
    for row in raw:
        a=latest[f'{scan.name}:{row["index"]}']
        row['level']=a.level
        row['enhancement_kind']=a.special;row['special']=a.special=='defined'
        row['substats']=[{'key':s.key,'value':float(s.value),'pending':s.pending} for s in a.stats]
        row['equip_raw']=a.equipped.removeprefix('UNKNOWN:') if a.equipped.startswith('UNKNOWN:') else a.equipped+'已装备' if a.equipped else ''
    output.mkdir(parents=True,exist_ok=True)
    save(output/'enhancer-artifacts.json',raw)
    save(output/'scan-count.json',{'requested':coverage['requested']})
    save(output/'snapshot-source.json',{'scan':str(scan.resolve()),'updates':str(updates) if updates else None,
                                      'note':'Reuse of recorded inventory, not a new game scan'})
    return output
