import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace

from enhancer.ui_server import Backend
from enhancer.batch import save
from enhancer.storage_management import inspect,clean,finish_cleanup
from test_ui import config


class ManagementTests(unittest.TestCase):
    def test_deleted_configuration_does_not_return_after_restart_or_stale_autosave(self):
        with TemporaryDirectory() as tmp:
            b=Backend(Path(tmp));entry=b.save_config({'config':config()});identifier=entry['id']
            b.store.save_draft('tab',1,{'config_id':identifier,'config':config()})
            b.manage_storage({'action':'archive','id':identifier})
            self.assertEqual(Backend(Path(tmp)).configurations(),[])
            self.assertIsNone(b.store.latest_draft())
            self.assertTrue(b.store.save_draft('tab',2,{'config_id':identifier,'config':config()})['ignored'])
            b.manage_storage({'action':'restore','id':identifier})
            self.assertEqual(len(b.configurations()),1)
            self.assertIsNotNone(b.store.latest_draft())
            b.manage_storage({'action':'archive','id':identifier})
            b.manage_storage({'action':'purge','id':identifier,'confirm':identifier})
            self.assertFalse((b.directory/'configs'/identifier).exists())
            self.assertEqual(Backend(Path(tmp)).configurations(),[])
            self.assertEqual(b.store.trash(),[])

    def test_purge_active_config_and_running_cleanup_are_rejected(self):
        with TemporaryDirectory() as tmp:
            b=Backend(Path(tmp));identifier=b.save_config({'config':config()})['id']
            with self.assertRaises(ValueError):b.manage_storage({'action':'purge','id':identifier,'confirm':identifier})
            self.assertTrue((b.directory/'configs'/identifier/'config.json').exists())
            b.process=SimpleNamespace(poll=lambda:None)
            with self.assertRaises(ValueError):b.manage_storage({'action':'archive','id':identifier})

    def test_history_is_bounded_and_identical_draft_is_not_duplicated(self):
        with TemporaryDirectory() as tmp:
            b=Backend(Path(tmp));identifier=b.save_config({'config':config()})['id']
            for i in range(30):
                data=config();data['name']=str(i);b.save_config({'id':identifier,'config':data})
            self.assertEqual(len(b.store.history(identifier)),20)
            b.store.save_draft('tab',1,{'config':data})
            b.store.save_draft('tab',2,{'config':data})
            self.assertEqual(len(b.store.draft_history('tab')),1)
            b.store.compact();self.assertEqual(b.store.config(identifier)['name'],'29')

    def test_cleanup_retains_failures_and_text_receipts_but_removes_success_images(self):
        with TemporaryDirectory() as tmp:
            r=Path(tmp);good=r/'observe-20261003-120000-000001';bad=r/'observe-20261003-120000-000002'
            for p in (good,bad):p.mkdir();(p/'game.png').write_bytes(b'image');save(p/'observation.json',{'text':{}})
            save(r/'receipt.json',{'evidence':[str(good)],'selection_proof':{'rarity_limit':4}})
            (r/'recognition.jsonl').write_text(json.dumps({'status':'retry','error':'unreadable','evidence':str(bad)})+'\n')
            preview=inspect(r);self.assertEqual(preview['count'],1)
            result=clean(r,preview);self.assertEqual(result['removed'],1)
            self.assertTrue((bad/'game.png').exists());self.assertTrue((good/'observation.json').exists());self.assertTrue((r/'receipt.json').exists())

    def test_pending_and_changed_references_block_cleanup(self):
        with TemporaryDirectory() as tmp:
            r=Path(tmp);p=r/'observe-20261003-120000-000001';p.mkdir();(p/'game.png').write_bytes(b'x')
            preview=inspect(r);save(r/'pending.json',{'evidence':str(p)})
            with self.assertRaises(ValueError):clean(r,preview)
            (r/'pending.json').unlink();save(r/'error.json',{'error':'bad','evidence':str(p)})
            with self.assertRaises(ValueError):clean(r,preview)
            self.assertTrue((p/'game.png').exists())

    def test_failed_job_retains_last_frames_without_explicit_exception_path(self):
        with TemporaryDirectory() as tmp:
            r=Path(tmp);p=r/'observe-20261003-120000-000001';p.mkdir();(p/'game.png').write_bytes(b'x')
            (r/'ui/jobs/job').mkdir(parents=True)
            finish_cleanup(r,{'id':'job','status':'failed','started':0})
            self.assertTrue((p/'game.png').exists())

    def test_links_outside_runtime_are_never_cleaned(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);r=root/'runtime';r.mkdir();outside=root/'outside';outside.mkdir();(outside/'game.png').write_bytes(b'x')
            link=r/'observe-20261003-120000-000001'
            try:link.symlink_to(outside,target_is_directory=True)
            except OSError:self.skipTest('Symlink creation unavailable')
            self.assertEqual(inspect(r)['count'],0)
            self.assertTrue((outside/'game.png').exists())
