import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch
import unittest
import uuid
import requests

from enhancer import good_backend as good
from test_import_inventory import good_artifact


WINDOW={'window':{'found':True,'width':1920,'height':1080}}

class GoodBackendTests(unittest.TestCase):
    def scan_receipt(self,client,root):
        client.state={'directory':str(root)}
        receipt={'jobId':'synthetic','complete':True,'termination':'Exhausted','expected':2,'visited':2,
                 'five_star':1,'accepted':1,'skipped_lower_rarity':1,'unknown_rarity':0,'missed':0,'skipped_positions':0}
        (Path(root)/'receipts').mkdir()
        (Path(root)/'receipts/scan-synthetic.json').write_text(json.dumps(receipt))
        return receipt

    def client(self,root):
        return good.GoodClient({'endpoint':'http://127.0.0.1:19265','token':'synthetic','instance':'test','directory':str(root)},root)

    def test_native_focus_loss_is_reported_without_replaying_input(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);job=str(uuid.uuid4());(root/'failures').mkdir()
            (root/'failures'/f'input-{job}.json').write_text(json.dumps({'jobId':job,'reason':'game_focus_lost','inputBlocked':True}))
            c=self.client(root);c.request=Mock(side_effect=[WINDOW,{'jobId':job}])
            with self.assertRaisesRegex(RuntimeError,'原神已失去前台焦点'):
                c.run('/equip',{'equip':[{}]},root/'job','equip')
            self.assertEqual(c.request.call_count,2)
            self.assertEqual(sum(v.args[0]=='POST' for v in c.request.call_args_list),1)
            self.assertEqual(json.loads((root/'job/request.json').read_text())['state'],'submitted')

    def test_only_owned_loopback_and_allowlisted_operations(self):
        for url in ('https://example.com','http://192.168.1.2:123','http://user@127.0.0.1:1','http://127.0.0.1:1/extra'):
            with self.assertRaises(ValueError):good.GoodClient({'endpoint':url,'token':'synthetic'})
        with TemporaryDirectory() as tmp:
            c=self.client(tmp);c.http.request=Mock()
            with self.assertRaises(ValueError):c.request('POST','/manage',{'unlock':[]})
            c.http.request.assert_not_called()

    def test_post_timeout_is_not_replayed(self):
        with TemporaryDirectory() as tmp:
            c=self.client(tmp);c.request=Mock(side_effect=[WINDOW,requests.ReadTimeout('synthetic timeout')])
            with self.assertRaises(requests.ReadTimeout):c.run('/equip',{'equip':[]},Path(tmp)/'job','equip')
            self.assertEqual(c.request.call_count,2)
            self.assertEqual(sum(c.args[0]=='POST' for c in c.request.call_args_list),1)
            state=json.loads((Path(tmp)/'job/request.json').read_text(encoding='utf-8'))
            self.assertEqual(state['state'],'submitting')

    def test_other_job_or_missing_result_never_counts_as_success(self):
        job=str(uuid.uuid4())
        with TemporaryDirectory() as tmp:
            c=self.client(tmp);c.request=Mock(side_effect=[WINDOW,{'jobId':job},{'jobId':str(uuid.uuid4()),'state':'completed'}])
            with self.assertRaisesRegex(RuntimeError,'编号'):c.run('/scan',{},Path(tmp)/'first','scan')
            c.request=Mock(side_effect=[WINDOW,{'jobId':job},{'jobId':job,'state':'completed'},{'results':[]}])
            with self.assertRaisesRegex(RuntimeError,'缺项'):c.run('/scan',{},Path(tmp)/'second','scan')

    def test_verify_cannot_accept_a_successful_mutating_result(self):
        job=str(uuid.uuid4())
        with TemporaryDirectory() as tmp:
            c=self.client(tmp);c.request=Mock(side_effect=[WINDOW,{'jobId':job},{'jobId':job,'state':'completed'},
                {'results':[{'id':'equip:0','status':'success'}]}])
            with self.assertRaisesRegex(RuntimeError,'未完成'):c.run('/equip',{'equip':[{}],'verifyOnly':True},Path(tmp)/'job','verify')

    def test_stop_blocks_new_input_and_explicit_next_job_can_clear_finished_stop(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir();(root/'runtime/stop.signal').touch()
            c=self.client(root);c.http.request=Mock()
            with self.assertRaisesRegex(RuntimeError,'中断'):c.request('POST','/scan',{})
            c.http.request.assert_not_called();good.prepare_game_job(root)
            self.assertFalse((root/'runtime/stop.signal').exists())

    def test_scan_uses_native_job_snapshot_and_keeps_metadata(self):
        with TemporaryDirectory() as tmp,patch.object(good,'ROOT',Path(tmp)):
            c=Mock();c.run.return_value=('synthetic',{'results':[{'id':'artifacts','status':'success'}]})
            self.scan_receipt(c,tmp)
            a=good_artifact();a['substats'][1]['initialValue']=5.2;c.request.return_value=[a]
            with patch.object(good,'ensure_backend',return_value=c):directory=good.scan_inventory()
            c.request.assert_called_with('GET','/artifacts?jobId=synthetic')
            rows=json.loads((directory/'enhancer-artifacts.json').read_text(encoding='utf-8'))
            self.assertEqual(rows[0]['enhancement_kind'],'ordinary')
            self.assertEqual(rows[0]['computed_roll_hints']['initial_values']['enerRech_'],5.18)
            self.assertEqual(rows[0]['import_metadata'],{})
            self.assertEqual(json.loads((directory/'scan-count.json').read_text())['scope'],'five_star')
            self.assertFalse((Path(tmp)/'runtime/session.json').exists())

    def test_missing_craft_metadata_does_not_publish_a_native_scan(self):
        with TemporaryDirectory() as tmp,patch.object(good,'ROOT',Path(tmp)):
            c=Mock();c.run.return_value=('synthetic',{});a=good_artifact();del a['elixirCrafted'];c.request.return_value=[a]
            self.scan_receipt(c,tmp)
            with patch.object(good,'ensure_backend',return_value=c),self.assertRaisesRegex(RuntimeError,'定制标记'):
                good.scan_inventory()
            self.assertFalse(list(Path(tmp).glob('runtime/goodscanner/scans/*/enhancer-artifacts.json')))

    def test_truncated_inventory_and_missing_five_star_never_pass_coverage(self):
        with TemporaryDirectory() as tmp:
            audit=self.scan_receipt(Mock(),tmp)
            good.validate_scan_audit(audit,'synthetic',1)
            for changes in ({'visited':1},{'five_star':2},{'accepted':0},{'unknown_rarity':1},
                            {'missed':1},{'skipped_positions':1},{'termination':'CallbackStop'},
                            {'jobId':'old'},{'complete':False},{'visited':True}):
                with self.subTest(changes=changes),self.assertRaises(RuntimeError):
                    good.validate_scan_audit(dict(audit,**changes),'synthetic',1)
            with self.assertRaises(RuntimeError):good.validate_scan_audit({},'synthetic',1)

    def test_duplicate_or_wrong_identity_receipts_are_rejected(self):
        a=good_artifact();b=good_artifact(slotKey='plume',mainStatKey='atk')
        payload={'equip':[{'artifact':a},{'artifact':b}]}
        def receipt(item):return {'jobId':'job','sameFrame':True,'ownerKey':'Odetta','expected':item,'ownerRaw':'奥黛塔已装备','matchDetails':'complete-frame fields'}
        self.assertEqual(len(good.validate_receipts([receipt(a),receipt(b)],payload,'job','Odetta')),2)
        with self.assertRaises(RuntimeError):good.validate_receipts([receipt(a),receipt(a)],payload,'job','Odetta')
        with self.assertRaises(RuntimeError):good.validate_receipts([receipt(a),receipt(b)],payload,'old-job','Odetta')

    def test_default_scan_and_equip_dispatch_to_goodscanner(self):
        from enhancer.workflow import fresh_scan
        from enhancer.equip import apply
        with patch.object(good,'scan_inventory',return_value='native-snapshot') as scanner:
            self.assertEqual(fresh_scan(),'native-snapshot');scanner.assert_called_once()
        with patch.object(good,'apply_loadouts',return_value={'status':'verified'}) as equip:
            self.assertEqual(apply('library',['a'],'scan')['status'],'verified')
            equip.assert_called_once_with('library',['a'],'scan',None,'strict',full_audit=False)


if __name__=='__main__':unittest.main()
