"""Regression cases for portable installs, cancellation and stale browser drafts."""
from collections import deque
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import Mock,patch

from enhancer.ui_server import Backend
from enhancer.ui_storage import LocalStore
from enhancer import good_backend as good
from enhancer.package_info import verify_package
from test_ui import config


class RecoveryTests(unittest.TestCase):
    @unittest.skipUnless(os.name=='nt','Windows process object lifetime')
    def test_exited_process_with_open_handle_does_not_block_restart(self):
        # Keep Popen (and its Windows process handle) alive for the entire check.
        # QueryFullProcessImageName can still succeed after wait() has returned.
        with TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'runtime/goodscanner/instances/exited';directory.mkdir(parents=True)
            stop=root/'runtime/stop.signal';stop.touch()
            p=subprocess.Popen([sys.executable,'-c','pass'],creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                p.wait(timeout=5)
                state={'state':'running','pid':p.pid,'exe_path':sys.executable,'directory':str(directory)}
                (root/'runtime/goodscanner/server.json').write_text(json.dumps(state),encoding='utf-8')
                self.assertIsNotNone(p._handle)
                self.assertFalse(good.owned_process_running(state))
                started=time.monotonic();good.prepare_game_job(root)
                self.assertLess(time.monotonic()-started,1)
                self.assertFalse(stop.exists())
                self.assertEqual(json.loads((root/'runtime/goodscanner/server.json').read_text(encoding='utf-8'))['state'],'stopped')
            finally:
                if p.poll() is None:p.kill()
                p.wait()

    @unittest.skipUnless(os.name=='nt','Windows process liveness')
    def test_live_process_is_not_mistaken_for_exited(self):
        p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            self.assertTrue(good.owned_process_running({'pid':p.pid,'exe_path':sys.executable}))
            self.assertFalse(good.owned_process_running({'pid':p.pid,'exe_path':str(Path(sys.executable).with_name('other.exe'))}))
        finally:p.kill();p.wait()

    def test_access_denied_is_not_treated_as_a_stopped_process(self):
        import ctypes
        kernel=Mock();kernel.OpenProcess.return_value=0
        with patch.object(good.os,'name','nt'),patch.object(good.ctypes,'WinDLL',return_value=kernel),patch.object(good.ctypes,'get_last_error',return_value=5):
            with self.assertRaisesRegex(RuntimeError,'Windows 错误 5'):good.owned_process_running({'pid':123,'exe_path':'synthetic.exe'})
        kernel.CloseHandle.assert_not_called()

    def test_scan_stall_stops_backend_without_publishing_partial_result(self):
        import itertools,uuid
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir();job=str(uuid.uuid4())
            client=good.GoodClient({'endpoint':'http://127.0.0.1:19265','token':'test','directory':str(root)},root)
            def request(method,path,*args):
                if path=='/workbench':return {'window':{'found':True,'width':1920,'height':1080}}
                if path=='/scan':return {'jobId':job}
                if path=='/status':return {'jobId':job,'state':'running','scanProgress':{'artifacts':{'completed':10,'total':200}}}
                raise AssertionError('No incomplete inventory may be requested')
            client.request=Mock(side_effect=request);clock=itertools.count(0,10)
            with patch.object(good.time,'monotonic',side_effect=lambda:next(clock)),patch.object(good.time,'sleep'),patch.object(good,'stop_owned_backend') as shutdown:
                with self.assertRaisesRegex(RuntimeError,'90 秒'):client.run('/scan',{},root/'job','scan')
            shutdown.assert_called_once_with(root)
            self.assertTrue((root/'runtime/stop.signal').exists())
            self.assertTrue((root/'job/stalled.json').exists())

    def test_missing_id_preserves_form_but_deleted_id_is_not_resurrected(self):
        with TemporaryDirectory() as tmp:
            b=Backend(tmp);data=config();old='b'*32
            result=b.save_config({'id':old,'config':data})
            self.assertTrue(result['recovered_missing']);self.assertNotEqual(result['id'],old)
            self.assertEqual(b.store.config(result['id'])['name'],data['name'])
            b.store.archive(result['id'])
            with self.assertRaisesRegex(ValueError,'已删除'):b.save_config({'id':result['id'],'config':data})
            self.assertEqual(b.store.configurations(),[])

    def test_database_identity_survives_restart_and_copy_not_replacement(self):
        import shutil
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'db';first=LocalStore(path).instance()
            self.assertEqual(first,LocalStore(path).instance())
            shutil.copy2(path,Path(tmp)/'copy');self.assertEqual(first,LocalStore(Path(tmp)/'copy').instance())
            path.unlink();self.assertNotEqual(first,LocalStore(path).instance())

    def test_new_job_cancelled_during_startup_cannot_clear_stop(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir();stop=root/'runtime/stop.signal';stop.touch()
            unique=root/'new-job.signal';unique.touch()
            with patch.dict(os.environ,ENHANCER_JOB_STOP_FILE=str(unique)),patch.object(good,'stop_owned_backend') as shutdown:
                with self.assertRaisesRegex(RuntimeError,'本次任务已中断'):good.prepare_game_job(root)
                shutdown.assert_not_called()
            self.assertTrue(stop.exists())

    def test_dead_legacy_session_does_not_block_restart(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'runtime').mkdir();(root/'runtime/stop.signal').touch()
            (root/'runtime/session.json').write_text(json.dumps({'state':'running','controller_pid':987654321}))
            good.prepare_game_job(root)
            self.assertFalse((root/'runtime/stop.signal').exists())

    def test_shutdown_never_kills_unrelated_process_with_reused_pid(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);directory=root/'runtime/goodscanner/instances/a';directory.mkdir(parents=True)
            (directory.parents[1]/'server.json').write_text(json.dumps({'pid':123,'directory':str(directory)}))
            p=Mock();p.exe.return_value=str(root/'bin/goodscanner/workbench_goodscanner.exe');p.cmdline.return_value=['unrelated-job']
            with patch.object(good,'owned_process_running',return_value=True),patch('psutil.Process',return_value=p):
                with self.assertRaisesRegex(RuntimeError,'身份无法核验'):good.stop_owned_backend(root,grace=0)
            p.kill.assert_not_called()

    def test_inherited_pipe_cannot_hold_completed_job_open(self):
        with TemporaryDirectory() as tmp:
            b=Backend(tmp);d=b.directory/'jobs'/('a'*32);d.mkdir(parents=True)
            event=threading.Event()
            def lines():event.wait(3);yield 'late log\n'
            p=Mock();p.stdout=lines();p.wait.return_value=0;p.poll.return_value=0
            job={'id':'a'*32,'kind':'preview','status':'running','stop_requested':False,'output':str(d/'result.json'),'logs':deque()}
            b.job=job;b.process=p
            try:
                started=time.monotonic();b.monitor(p,job,d)
                self.assertLess(time.monotonic()-started,1.5);self.assertEqual(job['status'],'completed');self.assertFalse(b.busy())
            finally:event.set();time.sleep(.05)

    def test_old_monitor_does_not_cancel_new_campaign(self):
        with TemporaryDirectory() as tmp:
            b=Backend(tmp);d=b.directory/'jobs'/('a'*32);d.mkdir(parents=True)
            old={'id':'a'*32,'kind':'start','status':'running','stop_requested':True,'output':str(d/'result.json'),'logs':deque()}
            b.job={'id':'b'*32,'status':'running'}
            p=Mock();p.stdout=[];p.wait.return_value=1
            with patch('enhancer.job_control.cancel_batch') as cancel:b.monitor(p,old,d)
            cancel.assert_not_called();self.assertEqual(b.job['status'],'running')

    def test_stop_waits_for_backend_then_unlocks_import(self):
        with TemporaryDirectory() as tmp:
            b=Backend(tmp);d=b.directory/'jobs'/('a'*32);d.mkdir(parents=True)
            p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            b.process=p;b.job={'id':'a'*32,'kind':'scan','status':'running','stop_requested':False,'logs':deque()}
            entered=threading.Event();release=threading.Event()
            def shutdown(*args):entered.set();release.wait(3)
            try:
                with patch.object(good,'stop_owned_backend',side_effect=shutdown):
                    self.assertTrue(b.stop()['stopping']);self.assertTrue(entered.wait(4))
                    self.assertTrue(b.busy());self.assertTrue(b.stopping)
                    with self.assertRaisesRegex(ValueError,'停止'):b.submit({'kind':'scan'})
                    release.set()
                    deadline=time.monotonic()+3
                    while b.stopping and time.monotonic()<deadline:time.sleep(.02)
                    self.assertFalse(b.busy());self.assertEqual(b.job['status'],'stopped')
            finally:
                release.set()
                if p.poll() is None:p.kill()
                p.wait()

    def test_package_fails_before_startup_when_file_missing_or_mixed(self):
        required=['ArtifactWorkbench.exe','bin/goodscanner/workbench_goodscanner.exe','bin/onnxruntime.dll',
                  'bin/goodscanner/VCRUNTIME140.dll','bin/goodscanner/MSVCP140.dll',
                  'data/goodscanner-mappings.json','web/index.html','web/app.js','web/inventory.js']
        with TemporaryDirectory() as tmp:
            root=Path(tmp);files={}
            for name in required:
                p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'test');files[name]=hashlib.sha256(b'test').hexdigest()
            (root/'build-manifest.json').write_text(json.dumps({'files':files,'release':'test'}))
            self.assertEqual(verify_package(root)['checked_files'],len(required))
            (root/'web/app.js').write_bytes(b'old-version')
            with self.assertRaisesRegex(RuntimeError,'版本不匹配'):verify_package(root)
            (root/'bin/onnxruntime.dll').unlink()
            with self.assertRaisesRegex(RuntimeError,'缺失'):verify_package(root)


if __name__=='__main__':unittest.main()
