"""Bounded cancellation of the current worker and its owned descendants."""
import json
import os
from pathlib import Path
import time


def stop_requested(root):
    unique=os.environ.get('ENHANCER_JOB_STOP_FILE')
    return (Path(root)/'runtime/stop.signal').exists() or bool(unique and Path(unique).exists())


def pending_operations(runtime):
    runtime=Path(runtime)
    # Only job journals, never files in another installation.
    paths=set(runtime.glob('campaign-*/step-*/pending.json'))|set(runtime.glob('batch-*/item-*/pending.json'))|set(runtime.glob('run-*/pending.json'))
    paths.add(runtime/'goodscanner/enhancement/pending.json')
    active=runtime/'active-run.json'
    if active.exists():
        try:paths.add(Path(json.loads(active.read_text(encoding='utf-8'))['run_directory'])/'pending.json')
        except (ValueError,KeyError,OSError):pass
    return [p for p in paths if p.is_file()]


def terminate_job(process,runtime,grace=1.5):
    import psutil
    runtime=Path(runtime)
    if process.poll() is not None:return
    children=[]
    try:children=psutil.Process(process.pid).children(recursive=True)
    except (psutil.Error,ValueError):pass
    until=time.monotonic()+grace
    while process.poll() is None and time.monotonic()<until:time.sleep(.05)
    if process.poll() is None:
        try:children+=psutil.Process(process.pid).children(recursive=True)
        except (psutil.Error,ValueError):pass
    # psutil guards against PID reuse. Never terminate an arbitrary process by name.
    seen=set()
    for child in reversed(children):
        if child.pid in seen:continue
        seen.add(child.pid)
        try:child.kill()
        except psutil.Error:pass
    if process.poll() is None:process.terminate()
    try:process.wait(timeout=2)
    except Exception:
        if process.poll() is None:process.kill()


def cancel_batch(runtime):
    from .batch import save
    runtime=Path(runtime);path=runtime/'active-batch.json';changed=False
    # A native rejection before confirmation is authoritative and needs no user
    # intervention. Unknown outcomes stay pending; successful receipts reconcile
    # through the ordinary receipt validator, never by resending input.
    from .good_enhancement import request_matches
    import uuid
    for pending in pending_operations(runtime):
        try:
            record=json.loads(pending.read_text(encoding='utf-8'))
            if record.get('backend')!='GOODScanner':continue
            uuid.UUID(record['id'])
            native=runtime/'goodscanner/enhancement'/f"{record['id']}.json"
            result=json.loads(native.read_text(encoding='utf-8'))
            if result.get('confirmed') is False and request_matches(result.get('request'),record.get('payload')):
                save(pending.with_name('cancelled-before-confirmation.json'),record);pending.unlink();changed=True
        except (ValueError,KeyError,OSError,TypeError):continue
    equip=runtime/'active-equip.json'
    if equip.exists():
        state=json.loads(equip.read_text(encoding='utf-8'))
        directory=Path(state.get('directory','')).resolve()
        if state.get('status')!='verified' and directory.is_relative_to(runtime.resolve()):
            pending=directory/'equip-pending.json'
            if pending.exists():
                # Equipment assignment is non-consuming and will be freshly
                # preflighted on the next explicitly requested plan.
                record=json.loads(pending.read_text(encoding='utf-8'));record.update(status='cancelled',cancelled_at=time.time())
                save(directory/'equip-cancelled.json',record);pending.unlink()
            state.update(status='cancelled');save(equip,state);changed=True
    if not path.exists():return changed
    state=json.loads(path.read_text(encoding='utf-8'))
    if state.get('status') in ('finished','finished-with-deferred','cancelled'):return changed
    state.update(status='cancelled',cancelled_at=time.time(),needs_reconciliation=bool(pending_operations(runtime)))
    save(path,state)
    return True


def recover_orphaned_jobs(runtime):
    """A restarted UI cancels only workers with its exact recorded request path."""
    import psutil
    from .batch import save
    runtime=Path(runtime);found=False
    for path in (runtime/'ui/jobs').glob('*/job.json'):
        try:job=json.loads(path.read_text(encoding='utf-8'))
        except (ValueError,OSError):continue
        if job.get('status')!='running':continue
        found=True;(runtime/'stop.signal').touch();(path.parent/'stop.signal').touch()
        try:
            process=psutil.Process(job['worker_pid']);args=process.cmdline()
            expected=str((path.parent/'request.json').resolve())
            if expected in args and 'enhancer.ui_worker' in args:
                for child in process.children(recursive=True):
                    try:child.kill()
                    except psutil.Error:pass
                process.kill()
        except (psutil.Error,KeyError,ValueError):pass
        job.update(status='stopped',stop_requested=True,finished=time.time(),restart_recovered=True);save(path,job)
    active=runtime/'active-batch.json'
    if active.exists():
        state=json.loads(active.read_text(encoding='utf-8'))
        if state.get('status') in ('running','prepared'):
            (runtime/'stop.signal').touch();state['status']='needs-attention';save(active,state)
    return found
