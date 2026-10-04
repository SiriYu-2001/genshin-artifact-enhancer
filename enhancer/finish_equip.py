"""Deterministic final loadout selection and post-campaign equipment queue."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

import requests

from .navigation import ROOT
from .batch import save
from .loadouts import save_allocation


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def select_final(allocation,scene=None):
    allowed=None
    scenes=allocation.get('scenarios')
    if scenes:
        if scene is None:
            if len(scenes)>1:raise ValueError('自动穿戴需要选定一个同时使用场景')
            scene=0
        if isinstance(scene,bool) or not isinstance(scene,int) or not 0<=scene<len(scenes):raise ValueError('穿戴场景无效')
        allowed=set(scenes[scene])
    chosen=[];skipped=[];characters=set();claimed=set()
    for row in allocation['demands']:
        aliases=set(row.get('effective_profile',{}).get('character_aliases',row.get('character_aliases',[])))|{row['character']}
        if allowed is not None and row['id'] not in allowed:reason='outside_selected_scene'
        elif aliases & characters:reason='higher_priority_loadout_for_same_character'
        elif row.get('status')!='ready' or len(row.get('items',[]))!=5:reason='no_complete_build'
        elif claimed & {a['id'] for a in row['items']}:reason='artifact_conflict_with_higher_priority'
        else:reason=None
        if reason:
            skipped.append({'id':row['id'],'name':row['name'],'reason':reason});continue
        chosen.append(row);characters.update(aliases);claimed.update(a['id'] for a in row['items'])
    return chosen,skipped


def allocate_active(inventory,campaign,names,scene=None):
    """Unused alternatives do not reserve gear during actual physical dressing."""
    from .campaign import Campaign,allocate
    allowed=None
    if campaign.scenarios:
        if scene is None:
            if len(campaign.scenarios)>1:raise ValueError('自动穿戴需要选择一个场景')
            scene=0
        if isinstance(scene,bool) or not isinstance(scene,int) or not 0<=scene<len(campaign.scenarios):raise ValueError('穿戴场景无效')
        allowed=set(campaign.scenarios[scene])
    selected=[];characters=set();skipped=[];result=None
    for demand in campaign.demands:
        aliases=set(demand.profile.data['character_aliases'])|{demand.profile.data['character']}
        if allowed is not None and demand.id not in allowed:reason='outside_selected_scene'
        elif aliases & characters:reason='higher_priority_loadout_for_same_character'
        else:reason=None
        if reason:
            skipped.append({'id':demand.id,'reason':reason});continue
        candidate=Campaign(selected+[demand],campaign.equipment,'priority',None,campaign.reserved_ids)
        trial=allocate(inventory,candidate,names)
        if trial['demands'][-1]['status']!='ready':
            skipped.append({'id':demand.id,'reason':'no_complete_build'});continue
        selected.append(demand);characters.update(aliases);result=trial
    if result is None:raise ValueError('没有可穿戴的完整配装')
    return result,skipped


def prepare_final(directory,scene=None):
    directory=Path(directory)
    allocation=read(directory/'plan.json')
    chosen,skipped=select_final(allocation,scene)
    if not chosen:raise ValueError('没有可自动穿戴的完整配装')
    # Save every feasible alternative, but equip just one per character.
    saved=deepcopy(allocation)
    saved['demands']=[r for r in saved['demands'] if r.get('status')=='ready' and len(r.get('items',[]))==5]
    path=directory/'loadouts-to-save.json';save(path,saved)
    save_allocation(path,ROOT/'runtime/loadouts/library.json')
    campaign_path=directory/'config/campaign.json'
    if campaign_path.exists():
        from .campaign import Campaign
        from .report import load_scan
        from .batch import apply_updates
        state=read(ROOT/'runtime/active-batch.json')
        if Path(state['directory'])!=directory:raise ValueError('Active campaign changed')
        pool,names,coverage=load_scan(state['scan_directory'])
        if not coverage['complete']:raise ValueError('Incomplete inventory')
        pool=apply_updates(pool,read(directory/'inventory-updates.json'))
        actual,skipped=allocate_active(pool,Campaign.load(campaign_path),names,scene)
        path=directory/'active-equipment-allocation.json';save(path,actual)
        save_allocation(path,ROOT/'runtime/loadouts/library.json')
        chosen=actual['demands']
    selection={'ids':[r['id'] for r in chosen],'characters':[r['character'] for r in chosen],'skipped':skipped}
    save(directory/'equip-selection.json',selection)
    return selection


def apply_finished(state,scene=None):
    if state['status'] not in ('finished','finished-with-deferred'):raise ValueError('强化尚未结束')
    directory=Path(state['directory'])
    if any((Path(r)/'pending.json').exists() for r in state['runs']):raise ValueError('尚有未确认强化')
    if (ROOT/'runtime/stop.signal').exists():raise ValueError('已请求停止，不自动换装')
    if state.get('kind')!='campaign':
        from .campaign import Campaign,Demand,allocate
        from .model import Profile
        from .report import load_scan
        from .batch import apply_updates
        import hashlib
        p=Profile.load(state['profile'])
        identifier='single-'+hashlib.sha256((p.data['character']+p.data['set_key']+p.data['name']).encode()).hexdigest()[:12]
        pool,names,coverage=load_scan(state['scan_directory'])
        if not coverage['complete']:raise ValueError('Incomplete inventory')
        pool=apply_updates(pool,read(directory/'inventory-updates.json'))
        campaign=Campaign([Demand(identifier,p)],'borrow' if state['ownership']=='borrow' else 'protected','priority',None,[])
        save(directory/'plan.json',allocate(pool,campaign,names))
    selection=prepare_final(directory,scene)
    from .equip import apply
    result=apply(ROOT/'runtime/loadouts/library.json',selection['ids'],state['scan_directory'],directory/'inventory-updates.json')
    save(directory/'auto-equip-result.json',result)
    return result


def watch(ui_job_id,directory,base='http://127.0.0.1:8766'):
    """Extend an already-running older worker without interrupting its game actions."""
    directory=Path(directory);marker=directory/'after-equip.json'
    if marker.exists() and read(marker).get('status') in ('submitted','verified'):return
    state={'enabled':True,'status':'waiting_for_enhancement','parent_job':ui_job_id}
    save(marker,state)
    session=requests.Session();session.trust_env=False
    while True:
        if not read(marker).get('enabled',False):return
        active=read(ROOT/'runtime/active-batch.json')
        if Path(active['directory'])!=directory:
            state.update(status='cancelled',reason='active_campaign_changed');save(marker,state);return
        if active['status'] in ('finished','finished-with-deferred') and not (ROOT/'runtime/stop.signal').exists():
            current=session.get(base+'/api/state',timeout=10).json()
            if not current['busy']:break
        time.sleep(2)
    if any((Path(p)/'pending.json').exists() for p in active['runs']):raise RuntimeError('Unresolved enhancement; equipment blocked')
    selection=prepare_final(directory)
    bootstrap=session.get(base+'/api/bootstrap',timeout=10).json();session.headers['X-Local-Token']=bootstrap['token']
    catalog=session.get(base+'/api/catalog',timeout=10).json()
    snapshot=next(s for s in catalog['snapshots'] if s['label']==Path(active['scan_directory']).name)
    response=session.post(base+'/api/jobs',json={'kind':'equip','ids':selection['ids'],'snapshot_id':snapshot['id']},timeout=20)
    response.raise_for_status();equip_job=response.json()['id']
    state.update(status='submitted',equip_job=equip_job,selection=selection);save(marker,state)
    output=ROOT/'runtime/ui/jobs'/equip_job/'result.json'
    while not output.exists():time.sleep(2)
    result=read(output);save(directory/'auto-equip-result.json',result)
    state['status']=result.get('status','failed');save(marker,state)


if __name__=='__main__':watch(sys.argv[1],sys.argv[2])
