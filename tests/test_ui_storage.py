import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from enhancer.ui_storage import LocalStore
from enhancer.ui_server import Backend
from enhancer.batch import save
from test_ui import config


class StorageTests(unittest.TestCase):
    def test_draft_survives_restart_and_keeps_incomplete_form(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'workbench.sqlite3';store=LocalStore(path)
            payload={'config':{'mode':'multi','demands':[{'character':'未填完','weight':None}]},'active_index':2}
            store.save_draft('browser',10,payload)
            restored=LocalStore(path).latest_draft()
            self.assertEqual(restored['payload'],payload)
            self.assertEqual(restored['revision'],1)

    def test_out_of_order_request_cannot_overwrite_newer_draft(self):
        with TemporaryDirectory() as tmp:
            store=LocalStore(Path(tmp)/'db.sqlite')
            store.save_draft('browser',20,{'config':{'name':'new'}})
            response=store.save_draft('browser',19,{'config':{'name':'old'}})
            self.assertTrue(response['ignored'])
            self.assertEqual(store.latest_draft()['payload']['config']['name'],'new')

    def test_versions_and_json_migration_keep_original_configs(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);identifier='a'*32;directory=root/'runtime/ui/configs'/identifier;directory.mkdir(parents=True)
            data=config();save(directory/'config.json',data)
            backend=Backend(root)
            self.assertEqual(backend.store.config(identifier),data)
            changed=config();changed['demands'][0]['profile']['weights']['atk']=.13
            backend.save_config({'id':identifier,'config':changed})
            versions=backend.store.history(identifier)
            self.assertEqual(len(versions),2)
            self.assertEqual(versions[-1]['config'],data)
            self.assertEqual(versions[0]['config']['demands'][0]['profile']['weights']['atk'],.13)
            self.assertEqual(Backend(root).store.config(identifier),versions[0]['config'])

    def test_draft_revision_history_is_bounded_and_recoverable(self):
        with TemporaryDirectory() as tmp:
            store=LocalStore(Path(tmp)/'db.sqlite')
            for i in range(105):store.save_draft('browser',i,{'config':{'name':str(i)}})
            history=store.draft_history('browser')
            self.assertEqual(len(history),20)
            self.assertEqual(history[0]['payload']['config']['name'],'104')
