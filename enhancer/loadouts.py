"""Local fixed loadouts and OCR attribute matching. No game inputs here."""
from collections import defaultdict
from copy import deepcopy
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

from .model import SLOTS
from .batch import save,apply_updates
from .report import load_scan


def signature(item):
    """Displayed attributes, independent of position, owner, lock and scan ID."""
    if not isinstance(item,dict):
        item={'set_key':item.set_key,'slot':item.slot,'rarity':item.rarity,'level':item.level,
              'main':item.main,'substats':[{'key':s.key,'value':float(s.value),'pending':s.pending} for s in item.stats]}
    stats=[]
    for s in item['substats']:
        stats.append({'key':s['key'],'value':round(float(s['value']),1 if s['key'].endswith('_') else 0),
                      'pending':s.get('pending',False)})
    return {'set_key':item.get('set_key',item.get('set')),'slot':item['slot'],
            'rarity':item.get('rarity',5),'level':item['level'],'main':item['main'],
            'substats':sorted(stats,key=lambda s:s['key'])}


def fingerprint(item):
    return hashlib.sha256(json.dumps(signature(item),sort_keys=True,separators=(',',':')).encode()).hexdigest()


def load_library(path):
    path=Path(path)
    if not path.exists():return {'version':1,'loadouts':{}}
    library=json.loads(path.read_text(encoding='utf-8-sig'))
    if library.get('version')!=1:raise ValueError('Unsupported loadout library version')
    return library


def save_allocation(allocation_path,library_path):
    """Append revisions. Multiple IDs may describe the same character."""
    allocation=json.loads(Path(allocation_path).read_text(encoding='utf-8-sig'))
    library=load_library(library_path)
    added=[]
    for row in allocation['demands']:
        items=row['items']
        if len(items)!=5 or {a['slot'] for a in items}!=set(SLOTS):
            raise ValueError(f"Incomplete loadout: {row['id']}")
        aliases=row.get('effective_profile',{}).get('character_aliases',[row['character']])
        entries=[]
        for a in items:
            sig=signature(a)
            if not sig['set_key'] or len(sig['substats'])!=4:raise ValueError('Incomplete OCR attributes')
            entries.append({'fingerprint':fingerprint(a),'attributes':sig,'name':a['name'],
                            'source_id':a['id'],'owner_hint':a.get('equipped','')})
        revisions=library['loadouts'].setdefault(row['id'],[])
        record={'revision':len(revisions)+1,'saved_at':datetime.now(timezone.utc).isoformat(),
                'name':row['name'],'character':row['character'],'character_aliases':aliases,
                'equipment_policy':allocation.get('equipment','protected'),
                'items':entries,'source_allocation':str(Path(allocation_path).resolve())}
        revisions.append(record);added.append({'id':row['id'],'revision':record['revision']})
    library_path=Path(library_path);library_path.parent.mkdir(parents=True,exist_ok=True)
    save(library_path,library)
    return added


def resolve(library,selected,inventory,policy='strict'):
    """Build a frozen desired assignment in selection priority order.

    strict: any unresolved loadout blocks all operations.
    priority: skip the whole unresolved/lower conflicting loadout, never steal
    from higher-priority targets or silently substitute a different item.
    """
    if policy not in ('strict','priority'):raise ValueError('Unknown conflict policy')
    if not selected or len(selected)!=len(set(selected)):raise ValueError('Select unique loadout IDs')
    buckets=defaultdict(list)
    for a in inventory:buckets[fingerprint(a)].append(a)
    claims={};characters=set();rows=[];operations=[];desired=[]
    for identifier in selected:
        entry=library['loadouts'][identifier][-1]
        aliases=set(entry.get('character_aliases',[]))|{entry['character']}
        errors=[];matched=[]
        if aliases & characters:errors.append({'reason':'character_has_another_selected_loadout'})
        if len(entry['items'])!=5 or {a['attributes']['slot'] for a in entry['items']}!=set(SLOTS):
            errors.append({'reason':'incomplete_saved_loadout'})
        for ref in entry['items']:
            if fingerprint(ref['attributes'])!=ref['fingerprint']:raise ValueError('Corrupt saved fingerprint')
            candidates=buckets[ref['fingerprint']]
            if len(candidates)!=1:
                errors.append({'slot':ref['attributes']['slot'],'reason':'missing_or_changed' if not candidates else 'ambiguous',
                               'matches':[a.id for a in candidates]})
                continue
            artifact=candidates[0]
            if artifact.id in claims:
                errors.append({'slot':artifact.slot,'reason':'claimed_by_higher_priority','demand':claims[artifact.id]})
            matched.append(artifact)
        row={'id':identifier,'character':entry['character'],'revision':entry['revision'],
             'status':'skipped' if errors else 'ready','errors':errors,'items':[]}
        if not errors:
            characters.update(aliases)
            for a in sorted(matched,key=lambda a:SLOTS.index(a.slot)):
                claims[a.id]=identifier
                item={'artifact_id':a.id,'fingerprint':fingerprint(a),'slot':a.slot,
                      'character':entry['character'],'character_aliases':sorted(aliases),
                      'previous_owner':a.equipped,'loadout_id':identifier,
                      'already_equipped':a.equipped in aliases}
                row['items'].append(item);desired.append(item)
                if not item['already_equipped']:operations.append(deepcopy(item))
        rows.append(row)
    blocked=policy=='strict' and any(r['errors'] for r in rows)
    return {'version':1,'status':'blocked' if blocked else 'ready-with-skips' if any(r['errors'] for r in rows) else 'ready',
            'policy':policy,'loadouts':rows,'desired':desired,'operations':[] if blocked else operations,
            'executed':False,'live_adapter_available':True}


def plan_file(library_path,selected,scan,updates=None,policy='strict'):
    inventory,_,coverage=load_scan(scan)
    if not coverage['complete']:raise ValueError('Incomplete inventory scan')
    if updates:
        changes=json.loads(Path(updates).read_text(encoding='utf-8-sig'))
        if set(changes)-{a.id for a in inventory}:raise ValueError('Updates belong to another scan')
        inventory=apply_updates(inventory,changes)
    result=resolve(load_library(library_path),selected,inventory,policy)
    result['scan_directory']=str(Path(scan).resolve())
    return result
