"""Persistent campaign scheduler. Reuses the proven serial single-item driver."""
from datetime import datetime
from pathlib import Path
import json
import subprocess
import sys
import time

from .navigation import ROOT, Navigation
from .batch import apply_updates, save
from .campaign import Campaign, allocate, plan, compare_claims
from .report import load_scan
from .text_identity import title_has_name


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def offline(config,scan,updates=None):
    inventory,names,coverage=load_scan(scan)
    if not coverage['complete']:raise RuntimeError('Incomplete inventory scan')
    if updates:
        changes=read(updates)
        if set(changes)-{a.id for a in inventory}:raise ValueError('Updates belong to a different scan')
        inventory=apply_updates(inventory,changes)
    return plan(inventory,Campaign.load(config),names)


def start(config,scan=None):
    campaign=Campaign.load(config)  # Validate everything before game interaction.
    manifest=ROOT/'runtime/active-batch.json'
    if manifest.exists():
        previous=read(manifest)
        if previous['status'] not in ('finished','finished-with-deferred') or any((Path(p)/'pending.json').exists() for p in previous['runs']):
            raise RuntimeError('Finish or resume the existing run before starting a campaign')
    from .workflow import fresh_scan
    scan=Path(scan) if scan else fresh_scan()
    inventory,names,coverage=load_scan(scan)
    if not coverage['complete']:raise RuntimeError('Incomplete inventory scan')
    directory=ROOT/'runtime'/datetime.now().strftime('campaign-%Y%m%d-%H%M%S-%f')
    directory.mkdir(parents=True)
    snapshot=campaign.snapshot(directory/'config')
    before=allocate(inventory,campaign,names)
    save(directory/'allocation-before.json',before)
    save(directory/'inventory-updates.json',{})
    state={'kind':'campaign','directory':str(directory),'config':str(snapshot),
           'scan_directory':str(scan.resolve()),'status':'prepared','runs':[],'jobs':[],
           'started':time.time(),'revision':0}
    if manifest.exists():manifest.replace(directory/'previous-batch.json')
    save(manifest,state)
    run()


def sync_updates(state):
    directory=Path(state['directory'])
    updates=read(directory/'inventory-updates.json')
    for job in state['jobs']:
        path=Path(job['run_directory'])/'target-state.json'
        if path.exists():
            observed=read(path)
            if str(observed['index'])!=job['target_id'].split(':')[-1]:raise RuntimeError('Mismatched observed target ID')
            updates[job['target_id']]=observed
    save(directory/'inventory-updates.json',updates)
    return updates


def execute_current(state,names):
    job=state['jobs'][-1]
    active_path=ROOT/'runtime/active-run.json'
    active={'scan_directory':state['scan_directory'],'profile':job['profile'],'ownership':job['ownership'],
            'target_id':job['target_id'],'run_directory':job['run_directory'],
            'inventory_updates_path':str(Path(state['directory'])/'inventory-updates.json'),
            'receipt_roots':state['runs'],'campaign':state['directory']}
    pending_path=Path(job['run_directory'])/'pending.json'
    if pending_path.exists():
        pending=read(pending_path)
        old=read(active_path)
        if pending['target_id']!=job['target_id'] or any(old.get(k)!=active[k] for k in
                ('scan_directory','profile','ownership','target_id','run_directory','inventory_updates_path','campaign')):
            raise RuntimeError('Pending operation belongs to a different job')
    save(active_path,active)
    for attempt in range(4):
        if (ROOT/'runtime/stop.signal').exists():
            state['status']='stopped'
            save(ROOT/'runtime/active-batch.json',state)
            return False
        result=subprocess.run([sys.executable,'-m','enhancer','run'],cwd=ROOT)
        sync_updates(state)
        if result.returncode==0:break
        print(json.dumps({'phase':'retry','demand':job['demand_id'],'target':job['target_id'],'attempt':attempt+1}),flush=True)
    if result.returncode:
        state['status']='needs-attention'
        save(ROOT/'runtime/active-batch.json',state)
        raise RuntimeError(f"Campaign item stopped: {job['run_directory']}")
    if pending_path.exists():raise RuntimeError('Child returned with an unresolved operation')
    if not (Path(job['run_directory'])/'decision-final.json').exists():
        raise RuntimeError('Child did not record a final policy decision')
    nav=Navigation()
    try:
        text=nav.observe('enhance-1920')['text']
        if not title_has_name(text['title'],names[job['target_id']]):raise RuntimeError('Unexpected item end screen')
        nav._navigation_click('close_material_picker',1840,48)
        nav.ensure_bag()
    finally:
        nav.yas().close()
    job['status']='completed'
    state['revision']+=1
    save(ROOT/'runtime/active-batch.json',state)
    return True


def report(state,current):
    directory=Path(state['directory'])
    before=read(directory/'allocation-before.json')
    initial={r['id']:r for r in before['demands']}
    demands=[{k:r[k] for k in ('id','character','name','status','score','independent_score','items','eligible_count','deferred')}
             for r in current['demands']]
    for row in demands:
        row['before']=initial[row['id']]['score']
        row['gain']=row['score']-row['before'] if row['score'] is not None and row['before'] is not None else None
    result={'kind':'campaign','status':state['status'],'equipment':current['equipment'],'allocation':current['allocation'],
            'demands':demands,'conflicts':current['conflicts'],'shared_items':current['shared_items'],
            'transfers':current['transfers'],'scenarios':current['scenarios'],'scenario_names':current['scenario_names'],
            'blocked_demands':current['blocked_demands'],'jobs':state['jobs'],'resource_accounting':False}
    save(directory/'summary.json',result)
    if state['status'] in ('finished','finished-with-deferred'):
        save(directory/'allocation-final.json',result)
    from .campaign_report import write_report
    write_report(result,directory/'RESULTS.md')



def run():
    manifest=ROOT/'runtime/active-batch.json'
    state=read(manifest)
    if state.get('kind')!='campaign':raise RuntimeError('Current run is not a campaign')
    directory=Path(state['directory'])
    campaign=Campaign.load(state['config'])
    original,names,coverage=load_scan(state['scan_directory'])
    if not coverage['complete']:raise RuntimeError('Incomplete inventory')
    if state['status'] in ('finished','finished-with-deferred'):return
    for job in state['jobs'][:-1]:
        if (Path(job['run_directory'])/'pending.json').exists():raise RuntimeError('An earlier job is unresolved')
    sync_updates(state)
    if state['jobs'] and state['jobs'][-1]['status']!='completed':
        if not execute_current(state,names):return
    previous=read(directory/'allocation-before.json')
    decision_cache={}
    while True:
        if (ROOT/'runtime/stop.signal').exists():
            state['status']='stopped';save(manifest,state);return
        inventory=apply_updates(original,sync_updates(state))
        current=plan(inventory,campaign,names,cache=decision_cache)
        save(directory/'plan.json',current)
        with (directory/'allocation-history.jsonl').open('a',encoding='utf-8') as f:
            f.write(json.dumps({'revision':state['revision'],'changes':compare_claims(previous['claims'],current['claims'])},ensure_ascii=False)+'\n')
        previous=current
        selected=current['next']
        if selected is None:
            deferred=bool(current['blocked_demands']) or any(r['deferred'] for r in current['demands'])
            state['status']='finished-with-deferred' if deferred else 'finished'
            save(manifest,state);report(state,current)
            print(json.dumps({'status':state['status'],'remaining_eligible':0,'directory':str(directory)},ensure_ascii=False),flush=True)
            return
        state['status']='running'
        report(state,current)
        candidate=selected['candidate']
        run_dir=directory/f"step-{len(state['jobs'])+1:04d}-{selected['demand_id']}-{candidate['id'].split(':')[-1]}"
        run_dir.mkdir()
        profile_path=run_dir/'profile.json'
        save(profile_path,selected['profile'])
        job={'demand_id':selected['demand_id'],'target_id':candidate['id'],'profile':str(profile_path),
             'ownership':'borrow' if campaign.equipment=='borrow' else 'no-borrow',
             'run_directory':str(run_dir),'status':'running','probability_lower':candidate['probability_lower']}
        state['jobs'].append(job);state['runs'].append(str(run_dir))
        save(manifest,state)
        print(json.dumps({'phase':'enhancing','demand':job['demand_id'],'candidate':candidate},ensure_ascii=False),flush=True)
        if not execute_current(state,names):return
