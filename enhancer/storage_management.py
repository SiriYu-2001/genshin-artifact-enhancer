"""Local storage inspection and explicit, reference-aware screenshot cleanup."""
import hashlib
import json
import re
import stat as stat_module
import time
from pathlib import Path

EVIDENCE = re.compile(r'(?:observe|read)-\d{8}-\d{6}-\d{6}')


def regular_tree(path, root):
    """Reject junctions/symlinks before following a local cleanup target."""
    root = root.resolve()
    if not path.resolve().is_relative_to(root):return False
    for p in (path,*path.parents):
        if p.resolve() == root:return True
        if p.is_symlink() or getattr(p.lstat(),'st_file_attributes',0)&0x400:return False
    return False


def inspect(runtime, days=0):
    if isinstance(days,bool) or not isinstance(days,int) or not 0<=days<=3650:
        raise ValueError('截图保留天数须为0–3650的整数')
    root=Path(runtime).resolve();files=[];protected=set();blocked=[];sizes={}
    # Do not traverse reparse points, including user-created directory junctions.
    import os
    def plain(path):
        info=path.lstat()
        return not (stat_module.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400)
    for directory,dirs,names in os.walk(root,followlinks=False):
        parent=Path(directory)
        dirs[:]=[n for n in dirs if plain(parent/n)]
        for name in names:
            path=parent/name
            if not plain(path):continue
            stat=path.stat();rel=path.relative_to(root)
            group='screenshots' if path.suffix.lower()=='.png' else 'database' if name.startswith('workbench.sqlite3') else 'records'
            sizes[group]=sizes.get(group,0)+stat.st_size
            if name in ('pending.json','equip-pending.json'):blocked.append('有尚未确认的操作记录')
            if path.suffix.lower() in ('.json','.jsonl','.log','.txt'):
                try:
                    content=path.read_text(encoding='utf-8-sig')
                    # Successful screenshots are disposable; small JSON receipts remain.
                    # Preserve only failure/retry/pending references, including tracebacks.
                    for line in content.splitlines():
                        if re.search(r'error|failed|retry|exception|needs-attention|cancelled',line,re.I):
                            protected.update(EVIDENCE.findall(line))
                    if name in ('pending.json','equip-pending.json','equip-cancelled.json','character-search.json'):
                        protected.update(EVIDENCE.findall(content))
                    if path.suffix.lower()=='.json':
                        data=json.loads(content)
                        if isinstance(data,dict) and (data.get('error') or data.get('status') in ('failed','needs-attention','stopped')):
                            protected.update(EVIDENCE.findall(content))
                    if name in ('active-batch.json','active-equip.json','session.json'):
                        data=json.loads(content)
                        if name=='active-batch.json' and data.get('status') not in ('finished','finished-with-deferred','cancelled'):blocked.append('有未完成的强化任务')
                        if name=='active-equip.json' and data.get('status') not in ('verified','cancelled'):blocked.append('有未完成的换装任务')
                        if name=='session.json' and data.get('busy'):blocked.append('扫描控制器正在运行')
                except (OSError,UnicodeError,ValueError):blocked.append('部分记录无法读取，不能确认引用关系')
            if (path.suffix.lower()=='.png' and len(rel.parts)==2
                    and EVIDENCE.fullmatch(rel.parts[0])):
                files.append((path,stat))
    cutoff=time.time()-days*86400
    eligible=[{'path':str(p.relative_to(root)).replace('\\','/'),'bytes':s.st_size,'modified_ns':s.st_mtime_ns}
              for p,s in files if p.parent.name not in protected and s.st_mtime<cutoff]
    eligible.sort(key=lambda x:x['path'])
    fingerprint=hashlib.sha256(json.dumps(eligible,sort_keys=True).encode()).hexdigest()
    return {'sizes':sizes,'days':days,'files':eligible,'count':len(eligible),
            'bytes':sum(x['bytes'] for x in eligible),'protected_screenshots':sum(p.parent.name in protected for p,s in files),
            'blocked':sorted(set(blocked)),'fingerprint':fingerprint}


def clean(runtime, preview):
    root=Path(runtime).resolve();current=inspect(root,preview['days'])
    if current['blocked']:raise ValueError('；'.join(current['blocked']))
    if current['fingerprint']!=preview['fingerprint']:raise ValueError('文件或引用已变化，请重新预览')
    removed=0;size=0
    for item in current['files']:
        path=root/item['path']
        if not regular_tree(path,root):raise ValueError('拒绝清理越界路径或目录链接')
        stat=path.stat()
        if stat.st_size!=item['bytes'] or stat.st_mtime_ns!=item['modified_ns']:raise ValueError('文件已变化，请重新预览')
        path.unlink();removed+=1;size+=stat.st_size
    return {'removed':removed,'bytes':size}


def finish_cleanup(runtime, job):
    """Run only once the worker exits. Failed tasks retain their last ten frames."""
    root=Path(runtime)
    cutoff=job.get('finished',time.time())
    if not next(root.glob('observe-*/game.png'),None) and not next(root.glob('read-*/*.png'),None):return {'removed':0,'bytes':0}
    if job.get('status') in ('failed','stopped'):
        frames=sorted((p for p in root.glob('observe-*/game.png')
                       if regular_tree(p,root.resolve()) and job['started']<=p.stat().st_mtime<=cutoff),
                      key=lambda p:p.stat().st_mtime)[-10:]
        path=root/'ui/jobs'/job['id']/'failure-evidence.json'
        path.write_text(json.dumps({'status':'failed','evidence':[str(p.parent) for p in frames]},ensure_ascii=False),encoding='utf-8')
    preview=inspect(root,0)
    if preview['blocked']:return {'skipped':preview['blocked']}
    removed=size=0
    for item in preview['files']:
        if item['modified_ns']>int(cutoff*1e9):continue
        path=root/item['path']
        if not regular_tree(path,root.resolve()):continue
        stat=path.stat()
        if stat.st_size!=item['bytes'] or stat.st_mtime_ns!=item['modified_ns']:continue
        path.unlink();removed+=1;size+=stat.st_size
    result={'removed':removed,'bytes':size}
    (root/'screenshot-cleanup.json').write_text(json.dumps({'at':time.time(),**result,
        'policy':'Successful screenshots removed; error evidence and text receipts retained.'}),encoding='utf-8')
    return result
