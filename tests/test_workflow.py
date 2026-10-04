import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from enhancer import workflow, batch
from enhancer.model import Profile
from enhancer.sets import SET_LABELS


class WorkflowTests(unittest.TestCase):
    def test_fresh_scan_to_batch_is_one_call_and_snapshots_profile(self):
        source=Path(__file__).resolve().parents[1]/'profiles/奥黛塔.json'
        with TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'runtime').mkdir(); scan=root/'scan'; scan.mkdir()
            calls=[]
            def scanned():
                calls.append('scan')
                return scan
            def report(*args):
                calls.append('baseline')
                return {'baseline_usable':True,'best_available_build':{'displayed_score':30}}
            def execute():
                calls.append('batch')
                manifest=json.loads((root/'runtime/active-batch.json').read_text(encoding='utf-8'))
                snapshot=Profile.load(manifest['profile'])
                self.assertEqual(snapshot.data['character'],'奥黛塔')
                self.assertEqual(snapshot.data['artifact_crit_rate_cap'],47)
                self.assertEqual(snapshot.weight('enerRech_'),0)
                self.assertEqual(snapshot.weight('atk'),.25)
                self.assertEqual(manifest['runs'],[])
                self.assertEqual(manifest['scan_directory'],str(scan.resolve()))
                self.assertNotEqual(Path(manifest['profile']),source)
            with patch.object(workflow,'ROOT',root),patch.object(workflow,'fresh_scan',side_effect=scanned),\
                 patch.object(workflow,'create_report',side_effect=report),\
                 patch('enhancer.__main__.ensure_controller'),patch.object(batch,'main',side_effect=execute):
                workflow.start(source,'borrow')
            self.assertEqual(calls,['scan','baseline','batch'])

    def test_new_run_never_replaces_an_unresolved_operation(self):
        source=Path(__file__).resolve().parents[1]/'profiles/奥黛塔.json'
        with TemporaryDirectory() as tmp:
            root=Path(tmp); runtime=root/'runtime'; runtime.mkdir()
            item=runtime/'item'; item.mkdir(); (item/'pending.json').write_text('{}')
            state={'status':'stopped','runs':[str(item)]}
            batch.save(runtime/'active-batch.json',state)
            with patch.object(workflow,'ROOT',root),patch.object(workflow,'fresh_scan') as scan:
                with self.assertRaises(RuntimeError):workflow.start(source,'borrow')
                scan.assert_not_called()
            self.assertEqual(json.loads((runtime/'active-batch.json').read_text()),state)

    def test_profile_set_labels_are_known(self):
        for path in (Path(__file__).resolve().parents[1]/'profiles').glob('*.json'):
            p=Profile.load(path)
            self.assertEqual(SET_LABELS[p.data['set_key']],p.data['set_label'])
