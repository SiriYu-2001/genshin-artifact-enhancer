"""Interrupted consumption stays protected without trapping offline work."""
from collections import deque
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import Mock,patch
import json
import unittest
import uuid

from enhancer import good_enhancement as executor
from enhancer.batch import save
from enhancer.job_control import pending_operations
from enhancer.ui_server import Backend
from test_ui import config
import test_good_enhancement


class RecalculationRecoveryTests(unittest.TestCase):
    def test_stopped_reconciliation_allows_scan_and_calculation_but_not_consumption(self):
        with TemporaryDirectory() as temp:
            b=Backend(Path(temp));run=b.runtime/'run-stopped';run.mkdir()
            save(run/'pending.json',{'unresolved':True})
            save(b.runtime/'active-batch.json',{'status':'cancelled','directory':str(run),'runs':[str(run)]})
            b.job={'id':'old','kind':'reconcile','status':'stopped','logs':deque(),
                   'output':str(run/'result.json'),'stop_requested':True}
            state=b.state()
            self.assertFalse(state['busy']);self.assertFalse(state['can_stop'])
            self.assertTrue(state['needs_reconciliation'])
            saved=b.save_config({'config':config()})
            fake=SimpleNamespace(pid=-1,stdout=[],poll=lambda:None)
            with patch.object(b,'snapshot',return_value={'path':'snapshot','ownership_stale':False}),\
                 patch('enhancer.ui_server.subprocess.Popen',return_value=fake) as popen,\
                 patch('enhancer.ui_server.threading.Thread'):
                for kind in ('start','resume'):
                    with self.assertRaisesRegex(ValueError,'核对'):
                        b.submit({'kind':kind,'config_id':saved['id'],'fresh':False})
                popen.assert_not_called()
                for kind in ('scan','preview'):
                    b.submit({'kind':kind,'config_id':saved['id'],'scope':'current'})
                    self.assertEqual(b.job['request']['kind'],kind)
                    b.process=None;b.job['status']='completed'
            self.assertTrue((run/'pending.json').exists())
            self.assertEqual(json.loads((b.runtime/'active-batch.json').read_text())['status'],'cancelled')

    def fixture(self,root,local_pending=True,committed=False):
        old=test_good_enhancement.GoodEnhancementTests();old.setUp()
        from dataclasses import replace
        target=replace(old.before,id='scan:1')
        operation=str(uuid.uuid4());payload=deepcopy(old.payload);payload['operationId']=operation
        result=deepcopy(old.result);result['request']=payload
        for field in ('before','after'):result[field].update(nextExp=3000,title='fixture')
        run=root/'runtime/run-interrupted';run.mkdir(parents=True)
        save(root/'runtime/active-run.json',{'target_id':target.id,'profile':'profile','run_directory':str(run),
             'scan_directory':'scan','inventory_updates_path':str(run/'updates.json')})
        if local_pending:save(run/'pending.json',{'backend':'GOODScanner','id':operation,'target_id':target.id,'payload':payload})
        native=root/'runtime/goodscanner/enhancement';native.mkdir(parents=True)
        save(native/'pending.json',{'request':payload,'materialProof':result['materialProof']})
        save(native/f'{operation}.json',result)
        if committed:save(run/f'receipt-{operation}.json',{'target_id':target.id,'native_receipt':result})
        return old.profile,target,run,native,operation,result

    def run_reconcile(self,root,profile,target):
        with patch.object(executor,'ROOT',root),patch.object(executor.Profile,'load',return_value=profile),\
             patch.object(executor,'load_scan',return_value=([target],{target.id:'fixture'},{'complete':True})),\
             patch.object(executor,'ensure_backend') as start:
            try:return executor.main(reconcile_only=True)
            finally:start.assert_not_called()

    def test_receipt_commit_recovers_both_markers_and_updates_inventory_without_input(self):
        for local_pending in (True,False):
            with self.subTest(local_pending=local_pending),TemporaryDirectory() as temp:
                root=Path(temp);profile,target,run,native,operation,result=self.fixture(root,local_pending,not local_pending)
                self.assertEqual(self.run_reconcile(root,profile,target)['status'],'reconciled')
                self.assertEqual(pending_operations(root/'runtime'),[])
                self.assertTrue((native/f'settled-{operation}.json').exists())
                self.assertEqual(json.loads((run/'updates.json').read_text())['scan:1']['level'],20)
                self.assertEqual(self.run_reconcile(root,profile,target)['status'],'reconciled')

    def test_invalid_receipt_or_unknown_native_marker_remains_blocking(self):
        for corrupt in ('receipt','foreign_native','no_local_commit'):
            with self.subTest(corrupt=corrupt),TemporaryDirectory() as temp:
                root=Path(temp);profile,target,run,native,operation,result=self.fixture(root,corrupt!='no_local_commit')
                if corrupt=='receipt':
                    result['materialProof']['visibleRarities']=[5];save(native/f'{operation}.json',result)
                if corrupt=='foreign_native':
                    payload=deepcopy(result['request']);payload['operationId']=str(uuid.uuid4());save(native/'pending.json',{'request':payload})
                with self.assertRaises(Exception):self.run_reconcile(root,profile,target)
                self.assertTrue((native/'pending.json').exists())
                self.assertTrue(pending_operations(root/'runtime'))

    def test_nothing_to_reconcile_does_not_require_old_profile_or_game(self):
        with TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(executor,'ROOT',root),patch.object(executor,'ensure_backend') as start:
                self.assertEqual(executor.main(reconcile_only=True)['status'],'reconciled')
                start.assert_not_called()


if __name__=='__main__':unittest.main()
