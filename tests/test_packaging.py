from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from unittest.mock import Mock
from enhancer.ui_server import public_report
from enhancer import yas_client

class PackagingTests(unittest.TestCase):
    def test_equipment_return_uses_permitted_actions_and_then_checks_bag(self):
        from enhancer.navigation import Navigation
        import time
        with TemporaryDirectory() as d:
            root=Path(d);(root/'runtime').mkdir()
            nav=Navigation();nav.observation={'window':{'left':0,'top':0}};nav.observed_at=time.monotonic();nav.directory=root
            nav.observe=Mock(side_effect=[{'text':{'compare':'对比','recommend':'圣遗物推荐','enhance':'强化','equip_action':'卸下'}},
                                          {'text':{'attributes':'属性','weapon':'武器','artifact':'圣遗物'}}])
            nav.wait_state=Mock();nav.ensure_bag=Mock()
            nav.api=Mock(side_effect=lambda method,path:[{'title':'原神','classname':'UnityWndClass','x':0,'y':0,'width':1920,'height':1080,'hWnd':123}] if method=='GET' else None)
            with patch('enhancer.navigation.ROOT',root),patch('enhancer.navigation.time.sleep'):
                nav.return_from_character_to_bag()
            nav.ensure_bag.assert_called_once()
            self.assertIn('close_equipment_selection',(root/'runtime/navigation.jsonl').read_text(encoding='utf-8'))

    def test_startup_requires_multiple_same_frame_labels(self):
        from enhancer.navigation import startup_page
        self.assertEqual(startup_page({'attributes':'属性','weapon':'武器','artifact':'圣遗物'}),'character')
        text={'compare':'对比','recommend':'圣遗物推荐','enhance':'强化','equip_action':'卸下'}
        self.assertEqual(startup_page(text),'equipment_selection')
        for key in text:
            self.assertIsNone(startup_page({k:v for k,v in text.items() if k!=key}))

    def test_bridge_start_and_restart_use_upstream_portable_flag(self):
        source=(Path(__file__).resolve().parents[1]/'tools/Start-Controller.ps1').read_text(encoding='utf-8')
        launches=[line for line in source.splitlines() if 'Start-Process -FilePath $frostflake' in line]
        self.assertEqual(len(launches),2)
        self.assertTrue(all('--stay' in line for line in launches))

    def test_public_reports_keep_longterm_results(self):
        for kind in ('dust','elixir'):
            r={'kind':kind,'longterm':{'horizons':[{'days':90}]},'private_test_field':'omit'}
            self.assertEqual(public_report(r)['longterm'],r['longterm'])
            self.assertNotIn('private_test_field',public_report(r))

    def test_packaged_ort_avoids_python_onnx_import(self):
        with TemporaryDirectory() as d:
            root=Path(d);(root/'bin').mkdir();(root/'bin/onnxruntime.dll').touch()
            with patch.object(yas_client,'ROOT',root),patch.dict('os.environ',{},clear=True):
                self.assertEqual(yas_client.runtime_environment()['ORT_DYLIB_PATH'],str(root/'bin/onnxruntime.dll'))

if __name__=='__main__':unittest.main()
