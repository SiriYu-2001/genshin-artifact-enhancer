"""Offline package identity and dependency checks; never downloads a repair."""
import hashlib
import json
from pathlib import Path


def build_info(root):
    try:
        manifest=json.loads((Path(root)/'build-manifest.json').read_text(encoding='utf-8'))
        return {'version':manifest.get('release','便携构建'), 'build':manifest.get('build'),
                'backend':'GOODScanner','offline_bundle':True}
    except (OSError,ValueError):return {'version':'开发版','backend':'GOODScanner','offline_bundle':False}


def verify_package(root):
    root=Path(root)
    manifest=json.loads((root/'build-manifest.json').read_text(encoding='utf-8'))
    files=manifest.get('files',{})
    required=['ArtifactWorkbench.exe','bin/goodscanner/workbench_goodscanner.exe','bin/onnxruntime.dll',
              'bin/goodscanner/VCRUNTIME140.dll','bin/goodscanner/MSVCP140.dll',
              'data/goodscanner-mappings.json','web/index.html','web/app.js','web/inventory.js']
    missing=[name for name in required if name not in files]
    broken=[]
    for name,digest in files.items():
        target=(root/name).resolve()
        if not target.is_relative_to(root.resolve()):raise RuntimeError('发行包清单路径无效')
        if not target.is_file():missing.append(name)
        elif name in required and hashlib.sha256(target.read_bytes()).hexdigest()!=digest:broken.append(name)
    if missing or broken:
        details='；'.join((['缺失：'+', '.join(missing[:8])] if missing else [])+
                         (['版本不匹配：'+', '.join(broken[:8])] if broken else []))
        raise RuntimeError('离线包不完整，'+details+'。请把整个 ZIP 解压到新目录，不要只复制 EXE 或覆盖旧程序；也请检查安全软件的隔离记录。无需另装 yas、霜华或 Python。')
    return {'checked_files':len(files),**build_info(root)}
