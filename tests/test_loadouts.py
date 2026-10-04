from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from enhancer.batch import save
from enhancer.model import SLOTS
from enhancer.loadouts import fingerprint,signature,save_allocation,load_library,resolve
from test_planner import piece


def inventory():
    return [piece('old:'+slot,slot) for slot in SLOTS]


def entry(pool,character='A'):
    return {'revision':1,'name':character,'character':character,'character_aliases':[character],
            'items':[{'fingerprint':fingerprint(a),'attributes':signature(a),'name':a.id,'source_id':a.id} for a in pool]}


class LoadoutTests(unittest.TestCase):
    def test_matching_survives_rescan_reorder_owner_and_lock_change(self):
        pool=inventory();lib={'loadouts':{'a':[entry(pool)]}}
        scanned=[replace(a,id='new:'+a.slot,equipped='B',locked=True,stats=tuple(reversed(a.stats))) for a in reversed(pool)]
        result=resolve(lib,['a'],scanned)
        self.assertEqual(result['status'],'ready')
        self.assertEqual(len(result['operations']),5)
        self.assertTrue(all(o['artifact_id'].startswith('new:') for o in result['operations']))

    def test_exact_duplicate_is_reported_not_arbitrarily_selected(self):
        pool=inventory();lib={'loadouts':{'a':[entry(pool)]}}
        result=resolve(lib,['a'],pool+[replace(pool[0],id='copy')])
        self.assertEqual(result['status'],'blocked')
        self.assertEqual(result['operations'],[])
        self.assertEqual(result['loadouts'][0]['errors'][0]['reason'],'ambiguous')

    def test_changed_attributes_are_not_fuzzy_matched(self):
        pool=inventory();lib={'loadouts':{'a':[entry(pool)]}}
        changed=[replace(pool[0],level=16)]+pool[1:]
        result=resolve(lib,['a'],changed)
        self.assertEqual(result['loadouts'][0]['errors'][0]['reason'],'missing_or_changed')
        self.assertEqual(result['operations'],[])

    def test_lower_priority_never_takes_a_higher_priority_artifact(self):
        pool=inventory();lib={'loadouts':{'a':[entry(pool,'A')],'b':[entry(pool,'B')]}}
        strict=resolve(lib,['a','b'],pool)
        self.assertEqual(strict['operations'],[])
        result=resolve(lib,['a','b'],pool,'priority')
        self.assertEqual(result['status'],'ready-with-skips')
        self.assertEqual({o['character'] for o in result['operations']},{'A'})
        self.assertEqual(result['loadouts'][1]['status'],'skipped')

    def test_already_equipped_uses_alias_and_needs_no_clicks(self):
        pool=inventory();e=entry(pool,'木偶');e['character_aliases']=['木偶','桑多涅']
        result=resolve({'loadouts':{'a':[e]}},['a'],[replace(a,equipped='桑多涅') for a in pool])
        self.assertEqual(result['status'],'ready')
        self.assertEqual(len(result['desired']),5)
        self.assertEqual(result['operations'],[])

    def test_library_preserves_revisions_and_same_character_multiple_loadouts(self):
        pool=inventory()
        items=[dict(signature(a),id=a.id,name=a.id,equipped='') for a in pool]
        row={'id':'a','name':'A-main','character':'A','items':items}
        with TemporaryDirectory() as tmp:
            p=Path(tmp);allocation=p/'allocation.json';library=p/'library.json'
            save(allocation,{'demands':[row]});save_allocation(allocation,library)
            save_allocation(allocation,library)
            row=deepcopy(row);row['id']='a-alternate'
            save(allocation,{'demands':[row]});save_allocation(allocation,library)
            result=load_library(library)
        self.assertEqual([r['revision'] for r in result['loadouts']['a']],[1,2])
        self.assertEqual(result['loadouts']['a-alternate'][0]['character'],'A')
