"""One game-driving Workbench worker across installations; computations remain parallel."""
import ctypes
import os


class GameLease:
    def __enter__(self):
        self.handle=None
        if os.name!='nt':return self
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateMutexW.argtypes=[ctypes.c_void_p,ctypes.c_bool,ctypes.c_wchar_p]
        kernel.CreateMutexW.restype=ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes=[ctypes.c_void_p,ctypes.c_ulong]
        kernel.CloseHandle.argtypes=[ctypes.c_void_p]
        kernel.ReleaseMutex.argtypes=[ctypes.c_void_p]
        handle=kernel.CreateMutexW(None,False,'Local\\ArtifactWorkbench.GameInput')
        if not handle:raise RuntimeError('无法建立游戏操作互斥锁')
        outcome=kernel.WaitForSingleObject(handle,0)
        if outcome not in (0,0x80):
            kernel.CloseHandle(handle)
            raise RuntimeError('另一份工坊正在操作游戏，请先结束那个任务')
        self.handle=handle;self.kernel=kernel
        return self

    def __exit__(self,*args):
        if self.handle:
            self.kernel.ReleaseMutex(self.handle);self.kernel.CloseHandle(self.handle)

