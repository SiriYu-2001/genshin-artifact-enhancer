"""Windows global emergency shortcut, independent of browser/game focus."""
import ctypes
from ctypes import wintypes
import os
import threading
import time


class EmergencyHotkey:
    label='未注册'
    identifier=0x4A31

    def __init__(self,callback,active=lambda:True):
        self.callback=callback;self.registered=False;self.error=None;self.thread_id=None
        self.active=active;self.win_registered=False;self.hook=None;self.win_down=set()
        self.last_win_seen=None;self.last_win_active=None;self.last_stop_requested=None
        self.ready=threading.Event();self.thread=None

    def start(self):
        if os.name!='nt':self.error='仅Windows支持全局快捷键';return
        self.thread=threading.Thread(target=self._run,daemon=True,name='artifact-emergency-hotkey')
        self.thread.start();self.ready.wait(2)

    def win_transition(self,vk,event):
        if vk not in (0x5B,0x5C):return
        if event in (0x0101,0x0105):self.win_down.discard(vk)
        elif event in (0x0100,0x0104) and vk not in self.win_down:
            self.win_down.add(vk)
            self.last_win_seen=time.time();self.last_win_active=bool(self.active())
            if self.last_win_active:ctypes.windll.user32.PostThreadMessageW(self.thread_id,0x8001,0,0)

    def poll_win(self,read_key):
        for vk in (0x5B,0x5C):self.win_transition(vk,0x0100 if read_key(vk)&0x8000 else 0x0101)

    def _run(self):
        user=ctypes.WinDLL('user32',use_last_error=True)
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.thread_id=kernel.GetCurrentThreadId()
        for label,mod,vk in (('Ctrl+Alt+F8',0x4003,0x77),('Ctrl+Shift+F8',0x4006,0x77),('Ctrl+Alt+F10',0x4003,0x79),('Ctrl+Shift+Pause',0x4006,0x13)):
            if user.RegisterHotKey(None,self.identifier,mod,vk):
                self.label=label;self.registered=True;break
        if not self.registered:self.error='备用快捷键全部被占用；请使用Win键或网页紧急停止按钮'
        # Observe only Win key transitions; never suppress keys or record other input.
        hookproc=ctypes.WINFUNCTYPE(ctypes.c_ssize_t,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM)
        user.CallNextHookEx.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM]
        user.CallNextHookEx.restype=ctypes.c_ssize_t
        user.SetWindowsHookExW.argtypes=[ctypes.c_int,hookproc,wintypes.HINSTANCE,wintypes.DWORD]
        user.SetWindowsHookExW.restype=wintypes.HANDLE
        kernel.GetModuleHandleW.argtypes=[wintypes.LPCWSTR];kernel.GetModuleHandleW.restype=wintypes.HMODULE
        def keyboard(code,event,data):
            if code>=0:
                vk=ctypes.cast(data,ctypes.POINTER(wintypes.DWORD))[0]
                self.win_transition(vk,event)
            return user.CallNextHookEx(self.hook,code,event,data)
        self._keyboard=hookproc(keyboard)
        self.hook=user.SetWindowsHookExW(13,self._keyboard,kernel.GetModuleHandleW(None),0)
        # Some foreground applications do not deliver the low-level hook callback.
        # Poll the actual held-state too, without injecting or swallowing input.
        user.GetAsyncKeyState.argtypes=[ctypes.c_int];user.GetAsyncKeyState.restype=ctypes.c_short
        self.win_registered=True
        self.ready.set();message=wintypes.MSG()
        try:
            running=True
            while running:
                while user.PeekMessageW(ctypes.byref(message),None,0,0,1):
                    if message.message==0x0012:running=False;break
                    if (message.message==0x0312 and message.wParam==self.identifier) or message.message==0x8001:
                        try:self.callback();self.last_stop_requested=time.time()
                        except Exception as exc:self.error=str(exc)
                if running:
                    self.poll_win(user.GetAsyncKeyState)
                    user.MsgWaitForMultipleObjects(0,None,False,10,0x04FF)
        finally:
            if self.registered:user.UnregisterHotKey(None,self.identifier)
            if self.hook:
                user.UnhookWindowsHookEx.argtypes=[wintypes.HANDLE];user.UnhookWindowsHookEx(self.hook)
            self.registered=False;self.win_registered=False

    def close(self):
        if self.thread_id and self.thread and self.thread.is_alive():
            ctypes.windll.user32.PostThreadMessageW(self.thread_id,0x0012,0,0)
            self.thread.join(timeout=2)

    def status(self):return {'shortcut':self.label,'registered':self.registered,'win_registered':self.win_registered,
                             'win_method':'hook_and_async_state_10ms','last_win_seen':self.last_win_seen,
                             'last_win_active':self.last_win_active,'last_stop_requested':self.last_stop_requested,'error':self.error}
