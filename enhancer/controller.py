"""Start and verify the package-owned elevated controller; no external installation fallback."""
import ctypes
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import requests

from .navigation import ROOT
from .yas_client import runtime_environment


def read_session(path):
    try:
        return json.loads(path.read_text(encoding='utf-8-sig'))
    except (OSError, ValueError):
        return {}


def controller_ready(state):
    if state.get('state') != 'running' or state.get('endpoint') != 'http://127.0.0.1:32333':
        return False
    if not state.get('token'):
        return False
    try:
        with requests.Session() as http:
            http.trust_env = False
            reply = http.get(state['endpoint']+'/api/windows', headers={
                'Origin':'http://127.0.0.1', 'Authorization':'Bearer '+state['token']}, timeout=2)
            return reply.status_code == 200 and isinstance(reply.json(), list)
    except (requests.RequestException, ValueError):
        return False


def ensure_controller():
    runtime = ROOT/'runtime'
    runtime.mkdir(exist_ok=True)
    path = runtime/'session.json'
    state = read_session(path)
    if controller_ready(state):
        if state.get('busy'):
            raise RuntimeError('扫描控制器正在执行其他任务，请等待结束后再试')
        return
    for relative in ('bin/cocogoat-control.exe', 'vendor/yas/target/release/yas_artifact.exe',
                     'vendor/yas/target/release/yas_readonly.exe', 'tools/Start-ControllerBootstrap.ps1'):
        if not (ROOT/relative).is_file():
            raise RuntimeError(f'发行包缺少 {relative}；请完整解压新版 ZIP，不要只复制 EXE')
    powershell = shutil.which('pwsh') or shutil.which('powershell')
    if not powershell:
        import os
        candidate = Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
        if candidate.is_file():
            powershell = str(candidate)
    if not powershell:
        raise RuntimeError('未找到 Windows PowerShell，无法启动扫描控制器')
    identifier = uuid.uuid4().hex
    args = ['-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',
            str(ROOT/'tools/Start-ControllerBootstrap.ps1'), '-OrtDll',runtime_environment()['ORT_DYLIB_PATH'],
            '-DurationMinutes','120','-SessionId',identifier]
    print(json.dumps({'phase':'controller_starting','message':'正在启动包内霜华与 yas；如出现管理员提示请允许。程序会自动切换到游戏，无需按 Win 键。'},ensure_ascii=False),flush=True)
    elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
    process = None
    if elevated:
        with (runtime/'controller-launch.log').open('wb') as log:
            process = subprocess.Popen([powershell,*args], cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    else:
        result = ctypes.windll.shell32.ShellExecuteW(None,'runas',powershell,subprocess.list2cmdline(args),str(ROOT),0)
        if result <= 32:
            raise RuntimeError('管理员授权被取消或启动失败；可右键以管理员身份启动工坊后重试')
    until = time.monotonic()+45
    while time.monotonic() < until:
        if (runtime/'stop.signal').exists():
            raise RuntimeError('已请求中断，控制器启动停止')
        state = read_session(path)
        if state.get('session_id') == identifier:
            if state.get('state') in ('failed','stopped'):
                raise RuntimeError('扫描控制器启动失败：'+state.get('reason','请查看 runtime/controller-startup-error.txt'))
            if controller_ready(state):
                print(json.dumps({'phase':'controller_ready','message':'包内控制器已通过连接与授权检查'},ensure_ascii=False),flush=True)
                return
        if process is not None and process.poll() is not None:
            raise RuntimeError('扫描控制器提前退出，请查看 runtime/controller-launch.log 或 controller-startup-error.txt')
        time.sleep(.2)
    raise RuntimeError('控制器未在限时内就绪；请查看 runtime/controller-startup-error.txt，并确认没有其他霜华实例占用端口')
