from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from enhancer.import_inventory import parse_document,commit_import
from enhancer.report import load_scan
from enhancer.ui_server import Backend
from enhancer.model import Profile
from enhancer.ui_config import materialize_snapshot


def good_artifact(**kwargs):
    return {'setKey':'HeartOfTheFurnace','slotKey':'flower','mainStatKey':'hp','rarity':5,'level':20,'location':'',
            'lock':True,'elixirCrafted':False,'totalRolls':9,
            'substats':[{'key':k,'value':v} for k,v in [('critRate_',18.7),('enerRech_',5.2),('eleMas',19),('def',19)]],**kwargs}


def document(items=None):
    return json.dumps({'format':'GOOD','version':3,'source':'yas-GOODScanner',
                       'artifacts':items if items is not None else [good_artifact()],
                       'uid':'private-account-fixture','characters':[{'private':'ignore'}],'weapons':[{'private':'ignore'}],'achievements':[1]})


class InventoryImportTests(unittest.TestCase):
    def test_goodscanner_v3_preview_crafted_alias_units_and_owner(self):
        a=good_artifact(level=0,totalRolls=3,location='Furina',setKey='SilkenMoonsSerenade')
        a['substats']=[{'key':k,'value':v} for k,v in [('critRate_',3.1),('enerRech_',5.2),('eleMas',19)]]
        a['unactivatedSubstats']=[{'key':'def','value':19}]
        rows,summary=parse_document(document([a]))
        self.assertTrue(summary['can_import']);self.assertEqual(rows[0]['setKey'],'SpinMoonSerenade')
        self.assertEqual(rows[0]['equip_raw'],'芙宁娜已装备');self.assertEqual(rows[0]['substats'][0]['value'],3.1)
        with TemporaryDirectory() as tmp:
            scan,_=commit_import(tmp,rows,summary,'test.json');items,_,_=load_scan(scan)
            self.assertEqual(items[0].random_rolls_left,4)
        del a['elixirCrafted'];a['elixerCrafted']=True
        rows,_=parse_document(document([a]));self.assertEqual(rows[0]['enhancement_kind'],'defined')

    def test_legacy_mona_percent_and_omit_not_confused_with_lock(self):
        item={'setName':'gladiatorFinale','position':'flower','star':5,'level':20,'mainTag':{'name':'lifeStatic','value':4780},
              'normalTags':[{'name':k,'value':v} for k,v in [('critical',.187),('recharge',.052),('elementalMastery',19),('defendStatic',19)]],
              'equip':None,'omit':True}
        rows,s=parse_document(json.dumps({'flower':[item]}))
        self.assertTrue(s['can_import']);self.assertEqual(rows[0]['setKey'],'GladiatorsFinale')
        self.assertEqual(rows[0]['substats'][0]['value'],18.7);self.assertTrue(rows[0]['excluded'])
        self.assertFalse(rows[0]['lock']);self.assertEqual(rows[0]['enhancement_kind'],'unknown')
        with TemporaryDirectory() as tmp:
            scan,_=commit_import(tmp,rows,s,'mona.json');artifacts,_,_=load_scan(scan)
            p=Profile.load(Path(__file__).resolve().parents[1]/'profiles/奥黛塔.json')
            self.assertFalse(p.allows(artifacts[0]))

    def test_invalid_five_star_blocks_entire_commit_low_rarity_is_reported(self):
        bad=good_artifact();bad['substats'][0]['value']=float('nan')
        with self.assertRaisesRegex(ValueError,'NaN'):parse_document(document([bad]))
        for change in ({'rarity':True},{'level':21},{'setKey':'NotASupportedSet'},{'location':5},{'lock':'false'}):
            rows,s=parse_document(document([good_artifact(),good_artifact(**change)]))
            self.assertEqual(s['error_count'],1);self.assertFalse(s['can_import'])
            with TemporaryDirectory() as tmp:
                with self.assertRaises(ValueError):commit_import(tmp,rows,s,'broken.json')
                self.assertFalse((Path(tmp)/'imports').exists())
        rows,s=parse_document(document([good_artifact(),{'rarity':4,'unsupported':'not used as fodder'}]))
        self.assertTrue(s['can_import']);self.assertEqual(s['skipped_non_five'],1);self.assertEqual(len(rows),1)

    def test_mona_slot_alias_and_malformed_main_tag(self):
        item={'setName':'gladiatorFinale','position':'plume','star':5,'level':20,'mainTag':{'name':'attackStatic','value':311},
              'normalTags':[{'name':k,'value':v} for k,v in [('critical',.187),('recharge',.052),('elementalMastery',19),('defendStatic',19)]]}
        rows,s=parse_document(json.dumps({'plume':[item]}));self.assertTrue(s['can_import']);self.assertEqual(rows[0]['slotKey'],'plume')
        for tag in ('bad',None,[],{'name':'attackStatic','value':0}):
            bad=deepcopy(item);bad['mainTag']=tag
            _,s=parse_document(json.dumps({'plume':[bad]}));self.assertEqual(s['error_count'],1)
        with self.assertRaisesRegex(ValueError,'两组别名'):parse_document(json.dumps({'plume':[item],'feather':[item]}))

    def test_inconsistent_stats_units_and_metadata_are_rejected(self):
        for mutate in (lambda a:a['substats'].append(a['substats'][0]),
                       lambda a:a['substats'][0].update(value=.187),
                       lambda a:a.update(unactivatedSubstats=[{'key':'atk','value':19}]),
                       lambda a:a.update(elixerCrafted=True),
                       lambda a:a.update(totalRolls=7)):
            a=good_artifact();mutate(a);_,s=parse_document(document([a]));self.assertFalse(s['can_import'])

    def test_unknowns_stay_unknown_and_initial_metadata_is_retained(self):
        a=good_artifact();del a['location'];del a['elixirCrafted']
        a['substats'][1]['initialValue']=5.18
        rows,s=parse_document(document([a]));self.assertTrue(s['can_import'])
        self.assertEqual(s['unknown_kind'],1);self.assertEqual(s['unknown_owner'],1)
        self.assertEqual(rows[0]['import_metadata'],{'upgrade_rolls':5,'initial_values':{'enerRech_':5.18}})
        with TemporaryDirectory() as tmp:
            scan,_=commit_import(tmp,rows,s,'unknown.json');artifacts,_,_=load_scan(scan)
            self.assertEqual(artifacts[0].special,'unknown');self.assertTrue(artifacts[0].equipped.startswith('UNKNOWN:'))
            copy=materialize_snapshot(scan,None,Path(tmp)/'copy');self.assertEqual(load_scan(copy)[0][0].special,'unknown')

    def test_goodscanner_display_initial_values_restore_unique_internal_tiers(self):
        a=good_artifact()
        for stat in a['substats'][1:]:stat['initialValue']=stat['value']
        rows,s=parse_document(document([a]));self.assertTrue(s['can_import'])
        self.assertEqual(rows[0]['import_metadata']['initial_values'],{'enerRech_':5.18,'eleMas':18.65,'def':18.52})
        a['substats'][1]['initialValue']=5.21
        _,s=parse_document(document([a]));self.assertFalse(s['can_import'])

    def test_duplicate_records_preserved_file_reimport_deduplicated_private_fields_dropped(self):
        rows,s=parse_document(document([good_artifact(),good_artifact()]))
        self.assertEqual(s['identical_extra'],1)
        with TemporaryDirectory() as tmp:
            directory,duplicate=commit_import(tmp,rows,s,'C:/private/folder/inventory.json');self.assertFalse(duplicate)
            second,duplicate=commit_import(tmp,rows,s,'same.json');self.assertTrue(duplicate);self.assertEqual(directory,second)
            raw=''.join(p.read_text(encoding='utf-8') for p in directory.iterdir())
            self.assertNotIn('private-account-fixture',raw);self.assertNotIn('weapons',raw);self.assertNotIn('C:/private',raw)
            artifacts,_,coverage=load_scan(directory);self.assertEqual(len(artifacts),2);self.assertTrue(coverage['complete'])
            self.assertNotEqual(artifacts[0].id,artifacts[1].id)

    def test_import_api_catalog_restart_and_busy_guard(self):
        with TemporaryDirectory() as tmp:
            b=Backend(Path(tmp));preview=b.import_inventory({'action':'preview','filename':'test.json','content':document()})
            self.assertFalse(b.catalog()['snapshots'])
            result=b.import_inventory({'action':'commit','preview':preview['preview']})
            catalog=Backend(Path(tmp)).catalog();s=catalog['snapshots'][0]
            self.assertEqual(s['id'],result['snapshot_id']);self.assertEqual(s['source'],'import');self.assertFalse(s['ownership_stale'])
            from unittest.mock import Mock
            b.process=Mock(poll=Mock(return_value=None))
            with self.assertRaisesRegex(ValueError,'任务运行中'):b.import_inventory({'action':'preview','content':document()})

    def test_presets_are_upserted_and_removable_without_touching_config(self):
        with TemporaryDirectory() as tmp:
            b=Backend(Path(tmp));profile=json.loads((Path(__file__).resolve().parents[1]/'profiles/奥黛塔.json').read_text(encoding='utf-8'))
            first=b.manage_presets({'action':'save','profile':profile})
            profile['threshold']=.1;second=b.manage_presets({'action':'save','profile':profile})
            self.assertEqual(first['id'],second['id']);self.assertEqual(len(second['presets']),1)
            self.assertEqual(Backend(Path(tmp)).store.presets()[0]['data']['threshold'],.1)
            b.manage_presets({'action':'delete','id':first['id']});self.assertEqual(b.store.presets(),[])


if __name__=='__main__':unittest.main()
