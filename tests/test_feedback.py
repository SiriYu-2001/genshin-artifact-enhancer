from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock,patch
import json
import os
import subprocess
import sys
import time
import unittest

from enhancer.ui_server import Backend
from enhancer.job_control import terminate_job,stop_requested,recover_orphaned_jobs
from enhancer.batch import replan,apply_updates
from enhancer.good_backend import GoodClient,validate_scan_audit
from enhancer.model import Profile,SLOTS
from test_planner import ROOT,piece
from test_ui import config


class FeedbackTests(unittest.TestCase):
    def test_missing_craft_flag_is_inspected_not_blanket_rejected(self):
        p=Profile.load(ROOT/'profiles/木偶.json');pool=[piece(s,s,hits=1) for s in SLOTS]
        target=replace(piece('old-json','flower',level=16,hits=4),special='unknown');pool.append(target)
        decisions=replan(pool,p,{a.id:a.id for a in pool})
        self.assertEqual(next(d for d in decisions if d['id']==target.id)['action'],'inspect')
        changed=apply_updates(pool,{target.id:{'level':16,'substats':[{'key':s.key,'value':float(s.value)} for s in target.stats],'enhancement_kind':'ordinary'}})
        self.assertEqual(changed[-1].special,'ordinary')

    def test_filtered_scope_is_never_full_account_coverage(self):
        audit={'jobId':'a','complete':True,'scope':'observed_filter_results','account_complete':False,
               'termination':'UnchangedPage','expected':2,'visited':2,'five_star':2,'accepted':2,
               'skipped_lower_rarity':0,'unknown_rarity':0,'missed':0,'skipped_positions':0,'inventory_header_count':2400}
        validate_scan_audit(audit,'a',2,'current')
        with self.assertRaises(RuntimeError):validate_scan_audit(audit,'a',2,'all')
        with self.assertRaises(RuntimeError):validate_scan_audit(dict(audit,missed=1),'a',2,'current')

    def test_resolution_failure_is_actionable_and_sends_no_input(self):
        with TemporaryDirectory() as tmp:
            c=GoodClient({'endpoint':'http://127.0.0.1:19265','token':'test'},tmp)
            c.request=Mock(return_value={'window':{'found':True,'width':2560,'height':1440}})
            with self.assertRaisesRegex(RuntimeError,'1920×1080.*2560×1440'):c.run('/scan',{},Path(tmp)/'job','scan')
            self.assertFalse(any(call.args[0]=='POST' for call in c.request.call_args_list))

    def test_cancelled_task_does_not_block_a_new_scan(self):
        with TemporaryDirectory() as tmp:
            b=Backend(Path(tmp));(b.runtime/'active-batch.json').write_text(json.dumps({'status':'needs-attention','runs':[],'directory':str(b.runtime/'campaign-test')}))
            self.assertTrue(b.stop()['stopped'])
            self.assertEqual(json.loads((b.runtime/'active-batch.json').read_text())['status'],'cancelled')
            fake=SimpleNamespace(pid=-1,stdout=[],poll=lambda:None)
            with patch('enhancer.ui_server.subprocess.Popen',return_value=fake),patch('enhancer.ui_server.threading.Thread'):
                job=b.submit({'kind':'scan','scope':'current'})
            self.assertEqual(b.job['request']['scope'],'current')
            self.assertEqual(len(job['id']),32)

    def test_unknown_consumption_survives_cancel_but_config_editing_is_free(self):
        with TemporaryDirectory() as tmp:
            b=Backend(Path(tmp));run=b.runtime/'run-test';run.mkdir();pending=run/'pending.json';pending.write_text('{"unresolved":true}')
            (b.runtime/'active-batch.json').write_text(json.dumps({'status':'needs-attention','runs':[str(run)],'directory':str(run)}))
            b.stop();self.assertTrue(pending.exists())
            saved=b.save_config({'config':config()});self.assertTrue(saved['id'])
            with patch.object(b,'snapshot',return_value={'ownership_stale':False,'path':'snapshot'}):
                with self.assertRaisesRegex(ValueError,'核对'):b.submit({'kind':'start','config_id':saved['id'],'fresh':False,'snapshot_id':'snapshot'})

    def test_cancelled_worker_cannot_send_input_after_global_stop_is_cleared(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir();marker=root/'old-job.signal';marker.touch()
            with patch.dict(os.environ,ENHANCER_JOB_STOP_FILE=str(marker)):
                self.assertTrue(stop_requested(root))
                c=GoodClient({'endpoint':'http://127.0.0.1:19265','token':'test'},root);c.http.request=Mock()
                with self.assertRaisesRegex(RuntimeError,'中断'):c.request('POST','/enhance',{})
                c.http.request.assert_not_called()

    def test_stubborn_owned_worker_is_terminated_within_bound(self):
        with TemporaryDirectory() as tmp:
            process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            try:
                started=time.monotonic();terminate_job(process,Path(tmp),grace=.05)
                self.assertIsNotNone(process.poll());self.assertLess(time.monotonic()-started,3)
            finally:
                if process.poll() is None:process.kill()
                process.wait()

    def test_orphan_recovery_never_kills_unrelated_pid(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);folder=root/'ui/jobs/a';folder.mkdir(parents=True)
            (folder/'job.json').write_text(json.dumps({'status':'running','worker_pid':123}))
            process=Mock();process.cmdline.return_value=['unrelated.exe']
            with patch('psutil.Process',return_value=process):recover_orphaned_jobs(root)
            process.kill.assert_not_called();process.children.assert_not_called()
            self.assertEqual(json.loads((folder/'job.json').read_text())['status'],'stopped')

    def test_live_source_classification_controls_whether_a_step_is_requested(self):
        from enhancer import good_enhancement as executor
        from enhancer.good_backend import InputNotSent
        for crafted in (True,False):
            with self.subTest(crafted=crafted),TemporaryDirectory() as tmp:
                root=Path(tmp);runtime=root/'runtime';runtime.mkdir();run=runtime/'run-test';run.mkdir()
                p=Profile.load(ROOT/'profiles/木偶.json')
                pool=[piece(f'scan:{i}',s,hits=1) for i,s in enumerate(SLOTS,1)]
                target=replace(piece('scan:6','flower',level=16,hits=4),special='unknown');pool.append(target)
                names={a.id:'target' for a in pool}
                (runtime/'active-run.json').write_text(json.dumps({'target_id':target.id,'profile':'fake','run_directory':str(run),'scan_directory':'scan'}))
                client=Mock();client.request.return_value={'enhancementVersion':2};actions=[]
                def native(path,payload,*args,**kwargs):
                    actions.append(payload['action'])
                    if payload['action']=='step':raise InputNotSent('test transport did not send input')
                    result={'request':payload,'confirmed':False}
                    if payload['action']=='open':result.update(sourceVerified=True,artifact=dict(payload['artifact'],elixirCrafted=crafted))
                    return 'test',{'results':[{'id':'enhance:0','status':'success','message':json.dumps(result)}]}
                client.run.side_effect=native
                with patch.object(executor,'ROOT',root),patch.object(executor,'load_scan',return_value=(pool,names,{'complete':True})),patch.object(executor.Profile,'load',return_value=p),patch.object(executor,'ensure_backend',return_value=client):
                    if crafted:executor.main()
                    else:
                        with self.assertRaises(InputNotSent):executor.main()
                self.assertEqual(actions,['open','leave'] if crafted else ['open','step'])
                self.assertFalse((run/'pending.json').exists())
                self.assertEqual(json.loads((run/'target-state.json').read_text())['enhancement_kind'],'defined' if crafted else 'ordinary')

if __name__=='__main__':unittest.main()
