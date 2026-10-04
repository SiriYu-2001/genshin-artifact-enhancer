import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock,patch

from enhancer import controller


class ControllerTests(unittest.TestCase):
    def test_running_flag_alone_never_means_ready(self):
        with patch.object(controller.requests,'Session') as factory:
            http=factory.return_value.__enter__.return_value
            http.get.return_value.status_code=403
            self.assertFalse(controller.controller_ready({'state':'running','endpoint':'http://127.0.0.1:32333','token':'synthetic'}))
            self.assertIs(http.trust_env,False)

    def test_never_send_token_to_nonlocal_endpoint(self):
        with patch.object(controller.requests,'Session') as factory:
            self.assertFalse(controller.controller_ready({'state':'running','endpoint':'https://example.com','token':'synthetic'}))
            factory.assert_not_called()

    def test_missing_bundled_dependency_is_immediate_actionable_error(self):
        with TemporaryDirectory() as d,patch.object(controller,'ROOT',Path(d)),patch.object(controller,'controller_ready',return_value=False):
            with self.assertRaisesRegex(RuntimeError,'bin/cocogoat-control.exe'):
                controller.ensure_controller()

    def test_elevated_launch_surfaces_bootstrap_error_without_second_uac(self):
        with TemporaryDirectory() as d:
            root=Path(d)
            for name in ('bin/cocogoat-control.exe','vendor/yas/target/release/yas_artifact.exe',
                         'vendor/yas/target/release/yas_readonly.exe','tools/Start-ControllerBootstrap.ps1'):
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.touch()
            def launch(command,**kwargs):
                identifier=command[-1]
                (root/'runtime/session.json').write_text(json.dumps({'state':'failed','session_id':identifier,'reason':'Existing external plugin'}))
                return Mock(poll=Mock(return_value=None))
            with patch.object(controller,'ROOT',root),patch.object(controller,'controller_ready',return_value=False),\
                 patch.object(controller.shutil,'which',return_value='powershell.exe'),\
                 patch.object(controller,'runtime_environment',return_value={'ORT_DYLIB_PATH':'synthetic.dll'}),\
                 patch.object(controller.ctypes.windll.shell32,'IsUserAnAdmin',return_value=True),\
                 patch.object(controller.ctypes.windll.shell32,'ShellExecuteW') as uac,\
                 patch.object(controller.subprocess,'Popen',side_effect=launch):
                with self.assertRaisesRegex(RuntimeError,'Existing external plugin'):
                    controller.ensure_controller()
                uac.assert_not_called()

    def test_controller_script_cannot_use_installed_machine_fallback(self):
        root=Path(__file__).resolve().parents[1]
        script=(root/'tools/Start-Controller.ps1').read_text(encoding='utf-8')
        self.assertNotIn('Program Files',script)
        self.assertIn('Bundled Frostflake',script)
        wrapper=(root/'tools/Start-ControllerBootstrap.ps1').read_text(encoding='utf-8')
        self.assertIn("state='failed'",wrapper)
        self.assertIn('controller-startup-error.txt',wrapper)


if __name__=='__main__':unittest.main()
