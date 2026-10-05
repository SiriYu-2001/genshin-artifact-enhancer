import ctypes
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import unittest
from unittest.mock import Mock,patch
from enhancer.hotkey import EmergencyHotkey
from enhancer.ui_server import Backend
from enhancer.navigation import Navigation

class HotkeyTests(unittest.TestCase):
    def test_emergency_idle_also_stops_controller(self):
        with TemporaryDirectory() as d:
            b=Backend(Path(d));b.emergency_stop()
            self.assertTrue((b.runtime/'stop.signal').exists())

    def test_advice_terminated_and_pending_preserved(self):
        with TemporaryDirectory() as d:
            b=Backend(Path(d));pending=b.runtime/'pending.json';pending.write_text('unconfirmed')
            b.process=Mock();b.process.poll.return_value=None;b.job={'kind':'dust'}
            b.emergency_stop();b.process.terminate.assert_called_once()
            self.assertTrue(b.job['stop_requested']);self.assertEqual(pending.read_text(),'unconfirmed')

    def test_input_blocked_before_bridge_call(self):
        with TemporaryDirectory() as d:
            root=Path(d);(root/'runtime').mkdir();(root/'runtime/stop.signal').touch()
            nav=Navigation();nav.session=Mock()
            with patch('enhancer.navigation.ROOT',root),self.assertRaisesRegex(RuntimeError,'Stop requested'):
                nav.api('POST','/api/mouse_event?dwFlags=2')
            nav.session.assert_not_called()

    @unittest.skipUnless(os.name=='nt','Windows global hotkey')
    def test_real_registration_message_dispatch_and_release(self):
        hit=threading.Event();hotkey=EmergencyHotkey(hit.set);hotkey.start()
        try:
            self.assertTrue(hotkey.registered,hotkey.error)
            self.assertTrue(hotkey.win_registered,hotkey.error)
            self.assertTrue(ctypes.windll.user32.PostThreadMessageW(hotkey.thread_id,0x0312,hotkey.identifier,0))
            self.assertTrue(hit.wait(2))
            hit.clear();hotkey.win_transition(0x5B,0x0101)
            hotkey.poll_win(lambda vk:0x8000 if vk==0x5B else 0)
            self.assertTrue(hit.wait(2))
            self.assertTrue(hotkey.last_win_active)
            # Dispatch a synthetic Win transition to our callback, not the desktop.
            hit.clear();hotkey.win_transition(0x5B,0x0101)
            hotkey.win_transition(0x5B,0x0100)
            self.assertTrue(hit.wait(2))
        finally:hotkey.close()
        self.assertFalse(hotkey.registered)

if __name__=='__main__':unittest.main()
