from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch
import json
import unittest

from enhancer import good_backend as good
from enhancer.batch import save
from enhancer.equipment_recovery import retry_equipment,recovery_state,queue_from_library
from enhancer.ui_server import Backend,public_report
from test_loadouts import inventory,entry
import test_single_pass_equipment as single_pass


class EquipmentRecoveryTests(unittest.TestCase):
    def prepare(self,root):
        client,result,error=single_pass.SinglePassEquipmentTests().exercise(root,timeout=True)
        self.assertIsNotNone(error)
        return good.read(root/'runtime/active-equip.json'),client

    def recovered_client(self,root):
        client=Mock();client.state={'directory':str(root/'native')}
        client.request.return_value={'inlineEquipVerification':True}
        def run(path,payload,directory,stage):
            self.assertEqual(path,'/equip');self.assertEqual(stage,'equip')
            Path(directory).mkdir(parents=True,exist_ok=True)
            for i,item in enumerate(payload['equip']):
                save(root/'native/receipts'/f'verify-recovered-{i}.json',{'jobId':'new-job','sameFrame':True,
                    'ownerKey':'Owner','ownerRaw':'木偶已装备','expected':item['artifact'],'matchDetails':'exact'})
            return 'new-job',{'results':[{'id':f'equip:{i}','status':'already_correct'} for i in range(5)]}
        client.run.side_effect=run
        return client

    def test_explicit_retry_archives_unknown_request_and_verifies_original_targets(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);state,_=self.prepare(root);d=Path(state['directory'])
            queue=good.read(d/'equipment-queue.json');queue[0]['payload']['allowBorrow']=False;save(d/'equipment-queue.json',queue)
            client=self.recovered_client(root)
            with patch.object(good,'ROOT',root),patch.object(good,'stop_owned_backend') as stop,patch.object(good,'ensure_backend',return_value=client):
                result=retry_equipment()
            stop.assert_called_once_with(root)
            self.assertEqual(result['status'],'verified');self.assertEqual(client.run.call_count,1)
            payload=client.run.call_args.args[1]
            self.assertFalse(payload['allowBorrow']);self.assertEqual(payload['equip'],queue[0]['payload']['equip'])
            self.assertFalse((d/'equip-pending.json').exists());self.assertEqual(len(list(d.glob('equip-retry-journal-*.json'))),1)
            self.assertTrue(all(i['status']=='already_correct' for i in result['loadouts'][0]['items']))

    def test_borrow_override_is_one_attempt_and_keeps_frozen_policy(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);state,_=self.prepare(root);d=Path(state['directory'])
            queue=good.read(d/'equipment-queue.json');queue[0]['payload']['allowBorrow']=False;save(d/'equipment-queue.json',queue)
            client=self.recovered_client(root)
            with patch.object(good,'ROOT',root),patch.object(good,'stop_owned_backend'),patch.object(good,'ensure_backend',return_value=client):
                retry_equipment(allow_borrow=True)
            self.assertTrue(client.run.call_args.args[1]['allowBorrow'])
            self.assertFalse(good.read(d/'equipment-queue.json')[0]['payload']['allowBorrow'])

    def test_running_backend_or_unconfirmed_consumption_never_clears_pending(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);state,_=self.prepare(root);d=Path(state['directory'])
            with patch.object(good,'ROOT',root),patch.object(good,'stop_owned_backend',side_effect=RuntimeError('still running')),patch.object(good,'ensure_backend') as ensure:
                with self.assertRaisesRegex(RuntimeError,'still running'):retry_equipment()
                self.assertTrue((d/'equip-pending.json').exists());ensure.assert_not_called()
            p=root/'runtime/run-test';p.mkdir();save(p/'pending.json',{})
            with patch.object(good,'ROOT',root),patch.object(good,'stop_owned_backend') as stop:
                with self.assertRaisesRegex(ValueError,'强化结果'):retry_equipment()
                stop.assert_not_called();self.assertTrue((d/'equip-pending.json').exists())

    def test_finished_campaign_exposes_equipment_recovery_without_enhancement_resume(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);self.prepare(root)
            save(root/'runtime/active-batch.json',{'status':'finished','runs':[]})
            state=Backend(root).state()
            self.assertFalse(state['can_resume']);self.assertTrue(state['equipment_recovery']['available'])
            self.assertFalse(state['needs_reconciliation'])

    def test_retry_submission_does_not_start_a_worker_for_unfinished_enhancement(self):
        with TemporaryDirectory() as temp:
            root=Path(temp);self.prepare(root)
            save(root/'runtime/active-batch.json',{'status':'needs-attention','runs':[]})
            backend=Backend(root)
            with patch('enhancer.ui_server.subprocess.Popen') as process:
                with self.assertRaisesRegex(ValueError,'结束当前强化任务'):backend.submit({'kind':'retry-equip'})
                process.assert_not_called()

    def test_legacy_recovery_uses_recorded_revision_and_exact_fingerprint(self):
        pool=inventory();old=entry(pool);new=deepcopy(old);new['revision']=2;new['items'][0]['attributes']['level']=16
        plan={'loadouts':[{'id':'one','revision':1,'status':'ready','items':[
            {'slot':r['attributes']['slot'],'fingerprint':r['fingerprint']} for r in old['items']]}]}
        with patch.object(good,'character_key',return_value='Owner'),patch.object(good,'set_key_to_good',side_effect=lambda x:x):
            queue=queue_from_library(plan,{'loadouts':{'one':[old,new]}})
            self.assertEqual(queue[0]['entry']['revision'],1)
            with self.assertRaisesRegex(ValueError,'版本已丢失'):queue_from_library(plan,{'loadouts':{'one':[new]}})
            plan['loadouts'][0]['items'][0]['fingerprint']='wrong'
            with self.assertRaisesRegex(ValueError,'属性不一致'):queue_from_library(plan,{'loadouts':{'one':[old]}})

    def test_review_report_keeps_threshold_names_reasons_and_error(self):
        result=public_report({'status':'failed','error':'equip failed','report':{'scores':{},'threshold':.05,
            'deferred':[{'id':'a','name':'花','level':16,'action':'reread','probability_lower':.049,'probability_upper':.051}]}})
        self.assertEqual(result['error'],'equip failed')
        item=result['review_items'][0];self.assertEqual(item['code'],'probability_boundary');self.assertEqual(item['threshold'],.05)
        self.assertEqual(result['deferred_count'],1)

    def test_missing_build_and_guarantee_state_have_distinct_remedies(self):
        result=public_report({'demands':[{'id':'one','name':'角色','deferred':[
            {'id':'a','reason':'defined/unknown artifact guarantee state unsupported'}]}],'blocked_demands':['one']})
        self.assertEqual([r['code'] for r in result['review_items']],['guarantee_unknown','missing_build'])

    def test_real_failure_after_four_skipped_slots_is_not_hidden(self):
        rows=[{'status':'skipped','message':'前一部位预检失败'} for _ in range(4)]
        rows.append({'status':'ui_error','message':'Borrowing is disabled'})
        self.assertEqual(good.failure_details(rows),'Borrowing is disabled')
        self.assertIn('原配装禁止借用',good.explain_failure(good.failure_details(rows)))

    def test_worker_keeps_enhancement_summary_when_final_equipment_fails(self):
        from enhancer import ui_worker
        with TemporaryDirectory() as temp:
            root=Path(temp);d=root/'runtime/batch-test';d.mkdir(parents=True)
            save(root/'runtime/active-batch.json',{'status':'finished-with-deferred','directory':str(d),'runs':[]})
            save(d/'summary.json',{'scores':{'borrow':{'before':1,'after':2,'gain':1}},'deferred':[]})
            save(d/'auto-equip-policy.json',{'enabled':True})
            request=root/'request.json';save(request,{'kind':'resume','output':str(root/'result.json')})
            with patch.object(ui_worker,'ROOT',root),patch.object(ui_worker.sys,'argv',['worker',str(request)]),\
                 patch('enhancer.batch.main') as batch,patch('enhancer.finish_equip.apply_finished',side_effect=RuntimeError('not found')):
                with self.assertRaisesRegex(RuntimeError,'not found'):ui_worker._main()
            result=good.read(root/'result.json')
            self.assertEqual(result['report']['scores']['borrow']['gain'],1)
            self.assertEqual(good.read(root/'runtime/active-batch.json')['status'],'finished-with-deferred')


if __name__=='__main__':unittest.main()
