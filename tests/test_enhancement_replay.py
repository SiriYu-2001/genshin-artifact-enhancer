"""Readable failure fixtures must not pass acceptance merely by stopping safely."""
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('enhancement_replay',Path(__file__).resolve().parents[1]/'tools/replay_enhancement.py')
replay=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay)


class EnhancementReplayTests(unittest.TestCase):
    def test_readable_unsafe_settings_must_match_the_annotated_reasons(self):
        for failures,should_pass in ((['low_rarity_filter_unverified'],True),([],False)):
            with TemporaryDirectory() as tmp:
                root=Path(tmp);cases=root/'cases.json';out=root/'out'
                cases.write_text(json.dumps([{'frame':'synthetic.png','required':True,'level':0,'count':0,
                    'artifact':{'name':'synthetic','setKey':'synthetic','slotKey':'plume','mainStatKey':'atk','level':0,'lock':True,'substats':[]},
                    'expected_observation':{'stageAdd':True,'lowRarityFilter':False,'stagePreconditionFailures':['low_rarity_filter_unverified']}}]),encoding='utf-8')
                result={'level':0,'materialCount':0,'stats':{},'stageAdd':True,'lowRarityFilter':False,'stagePreconditionFailures':failures}
                argv=['replay','--exe','backend.exe','--ort','ort.dll','--cases',str(cases),'--output',str(out)]
                with patch('sys.argv',argv),patch.object(replay.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(result))),patch('builtins.print'):
                    if should_pass:replay.main()
                    else:
                        with self.assertRaises(SystemExit):replay.main()

    def run_rejected(self,required,expected_rejection=None,exploratory=False):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);cases=root/'cases.json';out=root/'out'
            cases.write_text(json.dumps([{'frame':str(root/'frame.png'),'required':required,'expected_rejection':expected_rejection,'level':0,'count':2,
                'artifact':{'name':'synthetic','setKey':'synthetic','slotKey':'goblet','mainStatKey':'atk_',
                            'level':0,'lock':True,'substats':[]}}]),encoding='utf-8')
            argv=['replay','--exe',str(root/'backend.exe'),'--ort',str(root/'ort.dll'),
                  '--cases',str(cases),'--output',str(out)]
            if exploratory:argv.append('--exploratory')
            with patch('sys.argv',argv),patch.object(replay.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr='Unreadable material count fraction')),patch('builtins.print'):
                if (required and expected_rejection!='Unreadable material count fraction') or (not required and not exploratory):
                    with self.assertRaises(SystemExit) as failure:replay.main()
                    self.assertEqual(failure.exception.code,1)
                else:replay.main()
            return json.loads((out/'results.json').read_text(encoding='utf-8'))['summary']

    def test_readable_required_fixture_is_a_blocking_failure(self):
        self.assertEqual(self.run_rejected(True)['required_failed'],[0])

    def test_explicit_nonrequired_diagnostic_still_reports_rejection(self):
        result=self.run_rejected(False,exploratory=True)
        self.assertEqual(result['required_failed'],[])
        self.assertEqual(result['rejected'],1)
        self.assertEqual(result['unlabelled_cases'],[0])
        self.assertFalse(result['acceptance_passed'])

    def test_unlabelled_corpus_cannot_silently_pass_release_gate(self):
        result=self.run_rejected(False)
        self.assertFalse(result['acceptance_passed'])
        self.assertEqual(result['unlabelled_cases'],[0])

    def test_negative_fixture_requires_the_documented_rejection_reason(self):
        result=self.run_rejected(True,'Unreadable material count fraction')
        self.assertEqual(result['required_failed'],[])
        self.assertEqual(result['correct_rejections'],1)
        self.assertTrue(result['acceptance_passed'])
        result=self.run_rejected(True,'Five-star or unrecognized material')
        self.assertEqual(result['required_failed'],[0])

    def test_empty_corpus_is_not_acceptance(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);cases=root/'cases.json';cases.write_text('[]');out=root/'out'
            argv=['replay','--exe','backend.exe','--ort','ort.dll','--cases',str(cases),'--output',str(out)]
            with patch('sys.argv',argv),patch.object(replay.subprocess,'run') as run,patch('builtins.print'):
                with self.assertRaises(SystemExit):replay.main()
                run.assert_not_called()
            self.assertFalse(json.loads((out/'results.json').read_text())['summary']['acceptance_passed'])

    def test_tool_failure_cannot_count_as_expected_ocr_rejection(self):
        for failure in (replay.subprocess.TimeoutExpired('Unreadable material count fraction',30),
                        OSError('Unreadable material count fraction')):
            with TemporaryDirectory() as tmp:
                root=Path(tmp);cases=root/'cases.json';out=root/'out'
                cases.write_text(json.dumps([{'frame':'synthetic.png','required':True,'level':0,'count':0,
                    'expected_rejection':'Unreadable material count fraction',
                    'artifact':{'name':'synthetic','setKey':'synthetic','slotKey':'plume','mainStatKey':'atk','level':0,'lock':True,'substats':[]}}]))
                argv=['replay','--exe','backend.exe','--ort','ort.dll','--cases',str(cases),'--output',str(out)]
                with patch('sys.argv',argv),patch.object(replay.subprocess,'run',side_effect=failure),patch('builtins.print'):
                    with self.assertRaises(SystemExit):replay.main()
                summary=json.loads((out/'results.json').read_text())['summary']
                self.assertEqual(summary['infrastructure_failed'],[0])
                self.assertEqual(summary['correct_rejections'],0)
                self.assertFalse(summary['acceptance_passed'])


if __name__=='__main__':unittest.main()
