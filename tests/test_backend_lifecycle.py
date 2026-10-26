"""Offline fault injection: backend ownership, cancellation and lost replies."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
import uuid

import requests
from enhancer import good_backend as good


class BackendLifecycleTests(unittest.TestCase):
    def fixture(self,root,instance='old'):
        root=Path(root);exe=root/'bin/goodscanner/workbench_goodscanner.exe'
        exe.parent.mkdir(parents=True,exist_ok=True);exe.write_bytes(b'fixture-not-executable')
        mapping=root/'data/goodscanner-mappings.json';mapping.parent.mkdir(exist_ok=True);mapping.write_text('{}')
        directory=root/'runtime/goodscanner/instances'/instance;directory.mkdir(parents=True)
        state={'state':'running','pid':123,'instance':instance,'endpoint':'http://127.0.0.1:19265',
            'token':'synthetic','directory':str(directory),'exe_path':str(exe),
            'exe_hash':hashlib.sha256(exe.read_bytes()).hexdigest(),'revision':good.REVISION}
        good.save(root/'runtime/goodscanner/server.json',state)
        return state

    def startup_mocks(self,stack,clients):
        stack.enter_context(patch.object(good,'os',SimpleNamespace(name='nt',environ=os.environ,path=os.path)))
        stack.enter_context(patch.object(good.ctypes,'windll',Mock(shell32=Mock(IsUserAnAdmin=Mock(return_value=True))),create=True))
        stack.enter_context(patch.object(good,'GoodClient',side_effect=clients))
        stack.enter_context(patch.object(good.time,'sleep'))
        process=Mock(pid=456);process.poll.return_value=None
        def wait(timeout):process.poll.return_value=0;return 0
        process.wait.side_effect=wait
        spawn=stack.enter_context(patch.object(good.subprocess,'Popen',return_value=process))
        return process,spawn

    def test_healthy_instance_is_reused_without_restart(self):
        with TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);self.fixture(root);client=Mock();client.ready.return_value=True
            _,spawn=self.startup_mocks(stack,[client])
            shutdown=stack.enter_context(patch.object(good,'stop_owned_backend'))
            self.assertIs(good.ensure_backend(root),client)
            shutdown.assert_not_called();spawn.assert_not_called()

    def test_unhealthy_changed_or_partial_instance_stops_before_replacement(self):
        for prior,changed,health_check in [('running',False,True),('running',True,False),('starting',False,False),('failed',False,False)]:
            with self.subTest(state=prior,changed=changed),TemporaryDirectory() as tmp,ExitStack() as stack:
                root=Path(tmp);state=self.fixture(root);state['state']=prior
                if changed:state['exe_hash']='old-binary'
                good.save(root/'runtime/goodscanner/server.json',state)
                old=Mock();old.ready.return_value=False;new=Mock();new.ready.return_value=True
                _,spawn=self.startup_mocks(stack,[old,new] if health_check else [new])
                events=[]
                shutdown=stack.enter_context(patch.object(good,'stop_owned_backend',side_effect=lambda *a,**k:events.append('stopped')))
                original=spawn.return_value
                spawn.side_effect=lambda *a,**k:(events.append('spawned'),original)[1]
                self.assertIs(good.ensure_backend(root),new)
                self.assertEqual(events,['stopped','spawned'])
                shutdown.assert_called_once_with(root,expected_state=state)

    def test_unconfirmed_exit_never_overwrites_owner_or_spawns(self):
        with TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);state=self.fixture(root);client=Mock();client.ready.return_value=False
            _,spawn=self.startup_mocks(stack,[client])
            stack.enter_context(patch.object(good,'stop_owned_backend',side_effect=RuntimeError('exit unconfirmed')))
            with self.assertRaisesRegex(RuntimeError,'exit unconfirmed'):good.ensure_backend(root)
            self.assertEqual(good.read(root/'runtime/goodscanner/server.json'),state);spawn.assert_not_called()

    def test_cancellation_during_startup_stops_exact_spawned_process(self):
        with TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);self.fixture(root);(root/'runtime/goodscanner/server.json').unlink()
            client=Mock()
            def ready():(root/'runtime/stop.signal').touch();return False
            client.ready.side_effect=ready
            process,spawn=self.startup_mocks(stack,[client])
            with self.assertRaisesRegex(RuntimeError,'启动期间已请求中断'):good.ensure_backend(root)
            process.wait.assert_called_once_with(timeout=2);process.kill.assert_not_called()
            state=good.read(root/'runtime/goodscanner/server.json')
            self.assertEqual(state['state'],'failed');self.assertTrue((Path(state['directory'])/'shutdown.signal').exists())
            self.assertTrue((root/'runtime/stop.signal').exists());spawn.assert_called_once()

    def test_startup_failure_kills_unresponsive_exact_child_and_confirms_exit(self):
        with TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);self.fixture(root);(root/'runtime/goodscanner/server.json').unlink()
            client=Mock();client.ready.return_value=False
            process,_=self.startup_mocks(stack,[client])
            ticks=iter([0,41]);stack.enter_context(patch.object(good.time,'monotonic',side_effect=lambda:next(ticks)))
            process.wait.side_effect=[subprocess.TimeoutExpired('synthetic',2),0]
            process.kill.side_effect=lambda:setattr(process.poll,'return_value',0)
            with self.assertRaisesRegex(RuntimeError,'启动失败'):good.ensure_backend(root)
            process.kill.assert_called_once();self.assertEqual(process.wait.call_count,2)
            self.assertEqual(good.read(root/'runtime/goodscanner/server.json')['state'],'failed')

    def test_startup_failure_with_unconfirmed_exit_preserves_primary_and_owner(self):
        with TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);self.fixture(root);(root/'runtime/goodscanner/server.json').unlink()
            client=Mock();client.ready.return_value=False
            process,_=self.startup_mocks(stack,[client])
            ticks=iter([0,41]);stack.enter_context(patch.object(good.time,'monotonic',side_effect=lambda:next(ticks)))
            process.wait.side_effect=subprocess.TimeoutExpired('synthetic',2)
            with self.assertRaisesRegex(RuntimeError,'启动失败') as raised:good.ensure_backend(root)
            self.assertIn('退出未确认',' '.join(raised.exception.__notes__))
            self.assertEqual(good.read(root/'runtime/goodscanner/server.json')['state'],'starting')
            self.assertTrue((root/'runtime/stop.signal').exists())

    def test_ownership_persistence_failure_still_reaps_spawned_child(self):
        with TemporaryDirectory() as tmp,ExitStack() as stack:
            root=Path(tmp);self.fixture(root);(root/'runtime/goodscanner/server.json').unlink()
            client=Mock();process,_=self.startup_mocks(stack,[client])
            original=good.save
            def save(path,value):
                if Path(path).name=='server.json':raise OSError('disk full')
                return original(path,value)
            stack.enter_context(patch.object(good,'save',side_effect=save))
            with self.assertRaisesRegex(OSError,'disk full'):good.ensure_backend(root)
            process.wait.assert_called_once_with(timeout=2);client.ready.assert_not_called()

    def test_uncertain_submit_poll_or_result_stops_original_once_without_replay(self):
        job=str(uuid.uuid4());window={'window':{'found':True,'width':1920,'height':1080}}
        cases=(('submit',[window,requests.ReadTimeout('lost POST')]),
               ('poll',[window,{'jobId':job},requests.ReadTimeout('lost status')]),
               ('result',[window,{'jobId':job},{'jobId':job,'state':'completed'},requests.ConnectionError('lost result')]))
        for name,responses in cases:
            with self.subTest(stage=name),TemporaryDirectory() as tmp:
                root=Path(tmp);state=self.fixture(root);client=good.GoodClient(state,root)
                client.request=Mock(side_effect=responses)
                pending=root/'runtime/goodscanner/enhancement/pending.json';pending.parent.mkdir();pending.write_text('{}')
                with patch.object(good,'stop_owned_backend') as stop:
                    with self.assertRaises(requests.RequestException):client.run('/equip',{'equip':[{}]},root/'job','equip')
                stop.assert_called_once_with(root,expected_state=state)
                self.assertEqual(sum(c.args[0]=='POST' for c in client.request.call_args_list),1)
                self.assertTrue(pending.exists());self.assertTrue((root/'runtime/stop.signal').exists())
                self.assertTrue(good.read(root/'job/interrupted.json')['backend_exit_confirmed'])

    def test_positive_rejection_does_not_stop_or_retry(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);state=self.fixture(root);client=good.GoodClient(state,root)
            client.request=Mock(side_effect=[{'window':{'found':True,'width':1920,'height':1080}},good.InputNotSent('rejected')])
            with patch.object(good,'stop_owned_backend') as stop:
                with self.assertRaises(good.InputNotSent):client.run('/equip',{'equip':[]},root/'job','equip')
            stop.assert_not_called();self.assertFalse((root/'runtime/stop.signal').exists())

    def test_old_client_never_stops_new_instance_or_overwrites_its_record(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);old=self.fixture(root,'old');client=good.GoodClient(old,root)
            new=self.fixture(root,'new');new['pid']=789;good.save(root/'runtime/goodscanner/server.json',new)
            client.request=Mock(side_effect=[{'window':{'found':True,'width':1920,'height':1080}},requests.ReadTimeout('old reply lost')])
            with patch.object(good,'owned_process_running',return_value=False),patch('psutil.Process') as process:
                with self.assertRaises(requests.ReadTimeout):client.run('/equip',{'equip':[]},root/'job','equip')
            process.assert_not_called()
            self.assertEqual(good.read(root/'runtime/goodscanner/server.json'),new)
            self.assertTrue((Path(old['directory'])/'shutdown.signal').exists())
            self.assertFalse((Path(new['directory'])/'shutdown.signal').exists())
            self.assertFalse((root/'runtime/stop.signal').exists())

    def test_stop_failure_keeps_primary_error_and_pending(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp);state=self.fixture(root);client=good.GoodClient(state,root)
            client.request=Mock(side_effect=[{'window':{'found':True,'width':1920,'height':1080}},requests.ReadTimeout('primary timeout')])
            pending=root/'runtime/goodscanner/enhancement/pending.json';pending.parent.mkdir();pending.write_text('{}')
            with patch.object(good,'stop_owned_backend',side_effect=RuntimeError('secondary stop failure')):
                with self.assertRaisesRegex(requests.ReadTimeout,'primary timeout') as raised:client.run('/equip',{'equip':[]},root/'job','equip')
            self.assertIn('secondary stop failure',' '.join(raised.exception.__notes__))
            self.assertTrue(pending.exists());self.assertTrue((root/'runtime/stop.signal').exists())
            self.assertFalse(good.read(root/'job/interrupted.json')['backend_exit_confirmed'])


if __name__=='__main__':unittest.main()
