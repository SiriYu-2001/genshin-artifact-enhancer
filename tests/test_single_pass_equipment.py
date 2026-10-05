from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch
import unittest
import requests

from enhancer import good_backend as good
from enhancer.batch import save
from test_loadouts import inventory,entry


class SinglePassEquipmentTests(unittest.TestCase):
    def exercise(self,root,*,audit=False,capability=True,missing=False,timeout=False):
        pool=inventory();saved=entry(pool,'木偶');saved['equipment_policy']='borrow'
        plan={'status':'ready','loadouts':[{'id':'one','status':'ready','items':[{'artifact_id':a.id} for a in pool]}]}
        native=root/'native';(native/'receipts').mkdir(parents=True);(root/'runtime').mkdir()
        client=Mock();client.state={'directory':str(native)}
        client.request.return_value={'inlineEquipVerification':capability}
        def run(path,payload,directory,stage):
            Path(directory).mkdir(parents=True,exist_ok=True)
            if timeout:raise requests.ReadTimeout('ambiguous response')
            self.assertNotIn('preflightOnly',payload)
            jid='job-'+stage
            for i,row in enumerate(payload['equip']):
                if missing and i==4:continue
                save(native/'receipts'/f'verify-{stage}-{i}.json',{'jobId':jid,'sameFrame':True,'ownerKey':'Owner',
                     'ownerRaw':'木偶已装备','expected':row['artifact'],'matchDetails':'verified attributes'})
            return jid,{'results':[{'id':f'equip:{i}','status':'already_correct' if payload.get('verifyOnly') or i else 'success'} for i in range(5)]}
        client.run.side_effect=run
        with ExitStack() as stack:
            for name,value in [('ROOT',root),('ensure_backend',lambda:client),('character_key',lambda a:'Owner'),('set_key_to_good',lambda key:key)]:
                stack.enter_context(patch.object(good,name,value))
            stack.enter_context(patch('enhancer.loadouts.plan_file',return_value=plan))
            stack.enter_context(patch('enhancer.loadouts.load_library',return_value={'loadouts':{'one':[saved]}}))
            stack.enter_context(patch('enhancer.report.load_scan',return_value=(pool,{},{})))
            try:result=good.apply_loadouts('library',['one'],'snapshot',full_audit=audit)
            except Exception as error:return client,None,error
        return client,result,None

    def test_default_uses_one_native_traversal_with_five_inline_receipts(self):
        with TemporaryDirectory() as temp:
            client,result,error=self.exercise(Path(temp));self.assertIsNone(error)
            self.assertEqual(result['status'],'verified');self.assertEqual(client.run.call_count,1)
            self.assertEqual(len(result['loadouts'][0]['verified']),5)
            self.assertFalse(list(Path(temp).rglob('equip-pending.json')))

    def test_second_read_only_pass_is_explicitly_opt_in(self):
        with TemporaryDirectory() as temp:
            client,result,error=self.exercise(Path(temp),audit=True);self.assertIsNone(error)
            self.assertEqual(client.run.call_count,2)
            self.assertTrue(client.run.call_args_list[1].args[1]['verifyOnly'])

    def test_old_backend_never_falls_back_to_three_passes(self):
        with TemporaryDirectory() as temp:
            client,result,error=self.exercise(Path(temp),capability=False)
            self.assertIsInstance(error,RuntimeError);client.run.assert_not_called()

    def test_missing_receipt_does_not_claim_success_or_replay_equipment(self):
        with TemporaryDirectory() as temp:
            client,result,error=self.exercise(Path(temp),missing=True)
            self.assertIsInstance(error,RuntimeError);self.assertEqual(client.run.call_count,1)
            self.assertEqual(len(list(Path(temp).rglob('equip-pending.json'))),1)

    def test_timeout_leaves_pending_without_second_request(self):
        with TemporaryDirectory() as temp:
            client,result,error=self.exercise(Path(temp),timeout=True)
            self.assertIsInstance(error,requests.ReadTimeout);self.assertEqual(client.run.call_count,1)
            self.assertEqual(len(list(Path(temp).rglob('equip-pending.json'))),1)

if __name__=='__main__':unittest.main()
