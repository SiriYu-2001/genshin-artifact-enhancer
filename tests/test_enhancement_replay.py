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
    def run_rejected(self,required,expected_rejection=None):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);cases=root/'cases.json';out=root/'out'
            cases.write_text(json.dumps([{'frame':str(root/'frame.png'),'required':required,'expected_rejection':expected_rejection,'level':0,'count':2,
                'artifact':{'name':'synthetic','setKey':'synthetic','slotKey':'goblet','mainStatKey':'atk_',
                            'level':0,'lock':True,'substats':[]}}]),encoding='utf-8')
            argv=['replay','--exe',str(root/'backend.exe'),'--ort',str(root/'ort.dll'),
                  '--cases',str(cases),'--output',str(out)]
            with patch('sys.argv',argv),patch.object(replay.subprocess,'run',return_value=SimpleNamespace(returncode=1,stderr='Unreadable material count fraction')),patch('builtins.print'):
                if required and expected_rejection!='Unreadable material count fraction':
                    with self.assertRaises(SystemExit) as failure:replay.main()
                    self.assertEqual(failure.exception.code,1)
                else:replay.main()
            return json.loads((out/'results.json').read_text(encoding='utf-8'))['summary']

    def test_readable_required_fixture_is_a_blocking_failure(self):
        self.assertEqual(self.run_rejected(True)['required_failed'],[0])

    def test_explicit_nonrequired_diagnostic_still_reports_rejection(self):
        result=self.run_rejected(False)
        self.assertEqual(result['required_failed'],[])
        self.assertEqual(result['rejected'],1)

    def test_negative_fixture_requires_the_documented_rejection_reason(self):
        result=self.run_rejected(True,'Unreadable material count fraction')
        self.assertEqual(result['required_failed'],[])
        self.assertEqual(result['correct_rejections'],1)
        result=self.run_rejected(True,'Five-star or unrecognized material')
        self.assertEqual(result['required_failed'],[0])


if __name__=='__main__':unittest.main()
