"""Persistent inventory-wide queue; all decisions and game operations run locally."""
from dataclasses import replace
from pathlib import Path
import json
import subprocess
import sys
import time
import hashlib

from .navigation import ROOT, Navigation
from .model import Profile, Stat, UncertainObservation, evaluate
from .report import load_scan, select_build
from .capped import CappedInventory, build_score
from .text_identity import title_has_name


def save(path, value):
    path=Path(path)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temp.replace(path)


def apply_updates(inventory, updates):
    return [replace(a,level=updates[a.id]['level'],
                    stats=tuple(Stat.from_dict(s) for s in updates[a.id]['substats']),
                    equipped=updates[a.id].get('equipped',a.equipped))
            if a.id in updates else a for a in inventory]


def replan(inventory, profile, names,cache=None):
    policy=json.dumps(profile.data,sort_keys=True) if cache is not None else None
    mature=tuple(a for a in inventory if a.level==20 and profile.allows(a)) if cache is not None else None
    if cache is not None and cache.get('policy')==policy and cache.get('mature')==mature:
        baseline=cache['baseline']
    else:baseline=CappedInventory(inventory,profile)
    previous={}
    if cache is not None:
        # The complete mature bucket frontier fixes every baseline/complement
        # query. Include identity and all policy fields: reservation changes
        # and ownership restrictions must invalidate cached probabilities.
        context=(policy,tuple(sorted(baseline.buckets.items())))
        if cache.get('context')==context:previous=cache.get('decisions',{})
        else:cache.clear()
        cache['context']=context
        cache.update(policy=policy,mature=mature,baseline=baseline)
    updated={}
    decisions=[]
    for a in inventory:
        if a.level==20 or not profile.allows(a):continue
        if a.id in previous and previous[a.id][0]==a:
            decision=previous[a.id][1]
            decisions.append(dict(decision,name=names[a.id],level=a.level,set_key=a.set_key))
            updated[a.id]=(a,decision)
            continue
        try:
            decision=evaluate(a,inventory,profile,baseline)
            if a.special!='ordinary' and decision['action']=='reread':
                # Guarantees restrict which normal roll histories can occur.
                # If NO history can win, their unknown probabilities do not
                # matter. Never use a small nonzero ordinary probability here.
                unrestricted=evaluate(replace(a,special='ordinary'),inventory,profile,baseline)
                if unrestricted.get('probability_upper')==0:
                    decision=dict(unrestricted,action='retain',reason='no_improving_completion_even_without_guarantee_constraints',
                                  guarantee_probabilities_used=False)
        except UncertainObservation as exc:
            decision={'id':a.id,'action':'unsupported','reason':str(exc)}
        updated[a.id]=(a,decision)
        decisions.append(dict(decision,name=names[a.id],level=a.level,set_key=a.set_key))
    if cache is not None:cache['decisions']=updated
    return sorted(decisions,key=lambda d:d.get('probability_lower',-1),reverse=True)


def summaries(original, inventory, profile, names):
    result={}
    for mode in ('no-borrow','borrow'):
        p=Profile(dict(profile.data))
        p.data['allowed_equipped_characters']=['*'] if mode=='borrow' else p.data['character_aliases']
        builds=[select_build(pool,p) for pool in (original,inventory)]
        values=[float(build_score(b,p,displayed=True)) if b else None for b in builds]
        result[mode]={'before':values[0],'after':values[1],'gain':values[1]-values[0] if None not in values else None,
                      'items':[{'id':a.id,'name':names[a.id],'equipped':a.equipped} for a in (builds[1] or [])]}
    return result


def write_results(batch, original, inventory, profile, names, decisions):
    directory=Path(batch['directory'])
    receipts=sorted((json.loads(f.read_text(encoding='utf-8')) for root in batch['runs']
                     for f in Path(root).glob('receipt-*.json')),key=lambda r:r['finished'])
    scores=summaries(original,inventory,profile,names)
    before={a.id:a for a in original}
    changed=[a for a in inventory if a.level!=before[a.id].level or a.stats!=before[a.id].stats]
    items=[{'id':a.id,'name':names[a.id],'before_level':before[a.id].level,'after_level':a.level,
            'substats':[{'key':s.key,'value':float(s.value)} for s in a.stats]} for a in changed]
    result={'status':batch['status'],'scores':scores,'items':items,
            'confirmations':len(receipts),'resource_accounting':False,
            'remaining_eligible':[d for d in decisions if d['action']=='enhance'],
            'deferred':[d for d in decisions if d['action'] in ('reread','unsupported')]}
    save(directory/'summary.json',result)
    lines=[f"# {profile.data['character']}圣遗物整批实验",'',f"已培养 {len(items)} 件；确认强化 {len(receipts)} 次。按用户要求不统计消耗。",'',
           '| 模式 | 原始库存最优 | 当前库存最优 | 提升 |','|---|---:|---:|---:|']
    for mode,label in [('no-borrow','不借用'),('borrow','允许借用')]:
        s=scores[mode]
        values=[f"{s[k]:.4f}" if s[k] is not None else '无完整配装' for k in ('before','after','gain')]
        lines.append(f"| {label} | {' | '.join(values)} |")
    lines+=['',f"按平均词条计分，圣遗物暴击率（含主词条）计分上限 {profile.data['artifact_crit_rate_cap']}%。只计算最优搭配，未自动换装。",'',
            '| 圣遗物 | 等级 |','|---|---|']
    for a in items:lines.append(f"| {a['name']}（{a['id'].split(':')[-1]}） | +{a['before_level']} → +{a['after_level']} |")
    lines+=['',
            f"仍满足阈值 {len(result['remaining_eligible'])} 件；概率边界或机制状态待确认 {len(result['deferred'])} 件。",
            '',f"状态：{batch['status']}。"]
    (directory/'RESULTS.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    return result


def main():
    manifest=ROOT/'runtime/active-batch.json'
    if manifest.exists():
        batch=json.loads(manifest.read_text(encoding='utf-8'))
        if batch.get('kind')=='campaign':
            from .campaign_runtime import run
            return run()
    else:
        active=json.loads((ROOT/'runtime/active-run.json').read_text(encoding='utf-8'))
        directory=ROOT/'runtime'/time.strftime('batch-%Y%m%d-%H%M%S')
        directory.mkdir()
        previous=Path(active['run_directory'])
        updates={}
        if (previous/'target-state.json').exists():
            updates[active['target_id']]=json.loads((previous/'target-state.json').read_text(encoding='utf-8'))
        save(directory/'inventory-updates.json',updates)
        batch={k:active[k] for k in ('scan_directory','profile','ownership')}
        batch.update(directory=str(directory),runs=[str(previous)],status='running')
        save(manifest,batch)
    directory=Path(batch['directory'])
    updates_path=directory/'inventory-updates.json'
    original,names,coverage=load_scan(batch['scan_directory'])
    if not coverage['complete']:raise RuntimeError('Incomplete inventory')
    updates=json.loads(updates_path.read_text(encoding='utf-8'))
    for root in batch['runs']:
        if (Path(root)/'pending.json').exists():
            active=json.loads((ROOT/'runtime/active-run.json').read_text(encoding='utf-8'))
            pending=json.loads((Path(root)/'pending.json').read_text(encoding='utf-8'))
            if (Path(active['run_directory'])!=Path(root) or active['target_id']!=pending['target_id']
                    or active['target_id']!=batch.get('current')
                    or Path(active['profile'])!=Path(batch['profile'])
                    or active['ownership']!=batch['ownership']):
                raise RuntimeError(f'Pending operation does not match current batch: {root}')
            # Child first reads the result of this exact operation. It never
            # repeats the consuming click; only a verified result clears pending.
            for attempt in range(4):
                result=subprocess.run([sys.executable,'-m','enhancer','run'],cwd=ROOT)
                if result.returncode==0:break
            if result.returncode:raise RuntimeError(f'Unreconciled enhancement: {root}')
        state_path=Path(root)/'target-state.json'
        if state_path.exists():
            state=json.loads(state_path.read_text(encoding='utf-8'))
            updates[f"{Path(batch['scan_directory']).name}:{state['index']}"]=state
    save(updates_path,updates)
    if batch.get('current'):
        nav=Navigation()
        text=nav.observe('enhance-1920')['text']
        if title_has_name(text['title'],names[batch['current']]):
            nav._navigation_click('close_material_picker',1840,48)
            nav.ensure_bag()
        nav.yas().close()
    profile=Profile.load(batch['profile'])
    profile.data['allowed_equipped_characters']=['*'] if batch['ownership']=='borrow' else profile.data['character_aliases']
    while True:
        if (ROOT/'runtime/stop.signal').exists():
            batch['status']='stopped'
            save(manifest,batch)
            return
        updates=json.loads(updates_path.read_text(encoding='utf-8'))
        inventory=apply_updates(original,updates)
        stamp=hashlib.sha256(json.dumps({'updates':updates,'profile':profile.data,
              'scan_mtime':(Path(batch['scan_directory'])/'enhancer-artifacts.json').stat().st_mtime_ns},sort_keys=True).encode()).hexdigest()
        cache_path=directory/'plan-cache.json'
        cached=json.loads(cache_path.read_text(encoding='utf-8')) if cache_path.exists() else {}
        if cached.get('stamp')==stamp:
            decisions=cached['decisions']
        else:
            decisions=replan(inventory,profile,names)
            save(cache_path,{'stamp':stamp,'decisions':decisions})
        save(directory/'decisions.json',decisions)
        save(directory/'scores.json',summaries(original,inventory,profile,names))
        write_results(batch,original,inventory,profile,names,decisions)
        candidates=[d for d in decisions if d['action']=='enhance']
        print(json.dumps({'eligible':len(candidates),'candidates':[{k:d[k] for k in ('id','name','level','probability_lower')} for d in candidates]},ensure_ascii=False),flush=True)
        if not candidates:
            batch['status']='finished-with-deferred' if any(d['action'] in ('reread','unsupported') for d in decisions) else 'finished'
            save(manifest,batch)
            write_results(batch,original,inventory,profile,names,decisions)
            return
        candidate=candidates[0]
        run_dir=directory/('item-'+candidate['id'].split(':')[-1])
        run_dir.mkdir(exist_ok=True)
        if str(run_dir) not in batch['runs']:batch['runs'].append(str(run_dir))
        active={k:batch[k] for k in ('scan_directory','profile','ownership')}
        active.update(target_id=candidate['id'],run_directory=str(run_dir),batch=True,
                      inventory_updates_path=str(updates_path),receipt_roots=batch['runs'])
        save(ROOT/'runtime/active-run.json',active)
        batch.update(status='running',current=candidate['id'])
        save(manifest,batch)
        for attempt in range(4):
            if (ROOT/'runtime/stop.signal').exists():
                batch['status']='stopped'
                save(manifest,batch)
                return
            result=subprocess.run([sys.executable,'-m','enhancer','run'],cwd=ROOT)
            if result.returncode==0:break
            # Retry the local state machine, never resend a consuming click.
            # It re-observes the current UI and loads saved actual item state.
            # An outstanding confirmation is reconciled before anything else.
            print(json.dumps({'retry_item':candidate['id'],'attempt':attempt+1},ensure_ascii=False),flush=True)
            time.sleep(.5)
        if (run_dir/'target-state.json').exists():
            updates[candidate['id']]=json.loads((run_dir/'target-state.json').read_text(encoding='utf-8'))
            save(updates_path,updates)
        if result.returncode:
            batch['status']='needs-attention'
            save(manifest,batch)
            raise RuntimeError(f'Item stopped; evidence: {run_dir}')
        # Only leave enhancement when this entire artifact has finished its policy.
        nav=Navigation()
        state=nav.observe('enhance-1920')['text']
        if not title_has_name(state['title'],names[candidate['id']]):raise RuntimeError('Unexpected end screen')
        nav._navigation_click('close_material_picker',1840,48)
        nav.ensure_bag()
        nav.yas().close()


if __name__=='__main__':main()
