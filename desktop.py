"""Frozen Windows entrypoint; -m worker compatibility without system Python."""
import os
from pathlib import Path
import sys

ORIGINAL_ARGS=tuple(sys.argv[1:])


def main():
    import multiprocessing
    multiprocessing.freeze_support()
    args=sys.argv[1:]
    if args[:1]==['-u']:args=args[1:]
    root=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
    (root/'runtime').mkdir(exist_ok=True)
    os.chdir(root)
    # Console-less executables have no stdio; workers still use pipes when supplied.
    for name in ('stdout','stderr'):
        stream=getattr(sys,name)
        if stream is None or not args:
            setattr(sys,name,(root/'runtime/desktop.log').open('a',encoding='utf-8',buffering=1))
        elif hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace',line_buffering=True)
    if args[:1]==['-m']:
        module=args[1] if len(args)>1 else ''
        if module not in ('enhancer','enhancer.ui_worker'):raise ValueError('Unsupported worker module')
        sys.argv=[module,*args[2:]]
        if module=='enhancer.ui_worker':
            from enhancer.ui_worker import main as run
        else:
            from enhancer.__main__ import main as run
        run();return
    if args and args[0]!='--no-browser':
        sys.argv=['enhancer',*args]
        from enhancer.__main__ import main as run
        run();return
    import hashlib,json,socket,threading,time,urllib.request,webbrowser
    from enhancer.ui_server import serve
    identity=hashlib.sha256(str(root).encode()).hexdigest()[:16]
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def ours(url):
        try:
            with opener.open(url+'/api/health',timeout=1) as reply:return json.load(reply).get('instance')==identity
        except Exception:return False
    for port in range(8766,8787):
        url=f'http://127.0.0.1:{port}'
        if ours(url):
            if '--no-browser' not in args:webbrowser.open(url)
            return
        probe=socket.socket()
        try:probe.bind(('127.0.0.1',port))
        except OSError:continue
        finally:probe.close()
        break
    else:raise RuntimeError('No free local port in 8766–8786')
    if '--no-browser' not in args:
        def show():
            for _ in range(60):
                if ours(url):webbrowser.open(url);return
                time.sleep(.25)
        threading.Thread(target=show,daemon=True).start()
    serve(port)


if __name__=='__main__':
    try:main()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        try:
            root=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parent
            (root/'runtime').mkdir(exist_ok=True)
            with (root/'runtime/desktop.log').open('a',encoding='utf-8') as log:traceback.print_exc(file=log)
        except OSError:pass
        # main() changes sys.argv for workers; preserve the ORIGINAL launch mode.
        # A worker error must exit so the UI can finish the job, never show a modal.
        if not ORIGINAL_ARGS:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,f'启动失败：{exc}\n详情见程序目录 runtime/desktop.log。','圣遗物工坊',16)
        sys.exit(1)
