"""Build a private-data-free portable Windows folder and ZIP; never overwrite a build."""
import argparse
from datetime import datetime
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--yas-bin-dir',type=Path,default=ROOT/'vendor/yas/target/release')
    args=parser.parse_args()
    out=(args.output or ROOT/'dist'/datetime.now().strftime('%Y%m%d-%H%M%S')).resolve()
    if out.exists():raise RuntimeError('Use a new output directory; existing user data must never be removed')
    out.mkdir(parents=True)
    import onnxruntime
    ort=Path(onnxruntime.__file__).parent/'capi/onnxruntime.dll'
    # Frostflake is installed separately from its official publisher.
    for p in (ort,args.yas_bin_dir/'yas_artifact.exe',args.yas_bin_dir/'yas_readonly.exe'):
        if not p.is_file():raise FileNotFoundError(p)
    command=[sys.executable,'-m','PyInstaller','--onedir','--contents-directory','.',
             '--name','ArtifactWorkbench','--console','--hide-console','hide-early','--noupx',
             '--paths',str(ROOT),'--collect-submodules','enhancer',
             '--distpath',str(out),'--workpath',str(out/'build'),'--specpath',str(out/'spec')]
    for excluded in ('torch','tensorflow','scipy','matplotlib','pandas','onnxruntime','tkinter','IPython','notebook','pytest'):
        command+=['--exclude-module',excluded]
    for folder in ('web','layouts','data','profiles','campaigns'):
        command+=['--add-data',str(ROOT/folder)+':'+folder]
    for name in ('Start-Controller.ps1','patch_yas.py','yas_readonly.rs','Build-Yas.ps1'):
        command+=['--add-data',str(ROOT/'tools'/name)+':tools']
    for name in ('yas_artifact.exe','yas_readonly.exe'):
        command+=['--add-binary',str(args.yas_bin_dir/name)+':vendor/yas/target/release']
    command+=['--add-binary',str(ort)+':bin',str(ROOT/'desktop.py')]
    with (out/'build.log').open('w',encoding='utf-8') as log:
        subprocess.run(command,cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,check=True)
    app=out/'ArtifactWorkbench'
    # Preserve our source and relevant build patches; no inventory, tokens or images.
    source=app/'source';(source/'enhancer').mkdir(parents=True)
    for p in (ROOT/'enhancer').glob('*.py'):shutil.copy2(p,source/'enhancer'/p.name)
    shutil.copy2(ROOT/'desktop.py',source/'desktop.py')
    shutil.copy2(ROOT/'requirements.txt',source/'requirements.txt')
    shutil.copy2(__file__,source/'build_windows.py')
    docs=app/'docs';docs.mkdir()
    for name in ('资源规划使用指南.md','长期资源策略与数学模型.md','启圣之尘与霜尘规划.md','Windows便携版.md'):
        shutil.copy2(ROOT/'docs'/name,docs/name)
    licenses=app/'third-party';licenses.mkdir()
    for name in ('numpy','Pillow','requests','opencv-python','onnxruntime','PyInstaller','certifi','urllib3','charset-normalizer','idna'):
        dist=metadata.distribution(name)
        for item in dist.files or []:
            if any('license' in part.lower() or 'copying' in part.lower() for part in Path(item).parts) and Path(item).suffix.lower() not in ('.py','.pyc','.pyo','.pyd') and dist.locate_file(item).is_file():
                target=licenses/name/Path(item).name;target.parent.mkdir(exist_ok=True)
                shutil.copy2(dist.locate_file(item),target)
    pylicense=Path(sys.base_prefix)/'LICENSE.txt'
    if pylicense.exists():shutil.copy2(pylicense,licenses/'Python-LICENSE.txt')
    for name in ('LICENSE','THIRD_PARTY_NOTICES.md'):
        if (ROOT/name).exists():shutil.copy2(ROOT/name,app/name)
    if (ROOT/'licenses').exists():shutil.copytree(ROOT/'licenses',licenses/'upstream')
    (licenses/'SOURCES.txt').write_text('Project and matching yas source archive:\nhttps://github.com/SiriYu-2001/genshin-artifact-enhancer/releases\nyas upstream: https://github.com/1803233552/yas\nCommit: 614245fde088667216b80ff2133a7a44ebeb4d1f\nModified yas source and dependencies are provided as a separate Release asset.\nFrostflake is NOT bundled: install from https://cocogoat.work/extra/client\nSee THIRD_PARTY_NOTICES.md.\n',encoding='utf-8')
    (app/'开始使用.txt').write_text('圣遗物工坊 · Windows 便携预览版\n\n完整解压后双击 ArtifactWorkbench.exe，自动打开本地网页。不需要安装 Python 或 Agent。不要只复制 EXE。\n配置与扫描数据存入同目录 runtime，请备份整个 runtime。请放在有写权限的文件夹。\n游戏功能需要单独安装官方霜华：https://cocogoat.work/extra/client\n也可将官方 cocogoat-control.exe 放入 bin。霜华不随本包分发。\n游戏扫描/操作时按 Windows 提示授权管理员；只计算建议无需管理员。\n游戏适配：1920×1080、简体中文，从背包圣遗物界面开始。\n永不消耗五星圣遗物作为强化素材。霜/尘只提供建议。\n任务运行时按左/右Win键或网页显示的备用快捷键中断，也可点击紧急中断。\n升级保留 runtime；不要公开上传使用后的整个目录。\n',encoding='utf-8-sig')
    files=[p for p in app.rglob('*') if p.is_file()]
    assert not (app/'runtime').exists()
    manifest={'build':datetime.now().isoformat(),'python':sys.version,'pyinstaller':metadata.version('PyInstaller'),
              'files':{str(p.relative_to(app)).replace('\\','/'):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    (app/'build-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    archive=out/'ArtifactWorkbench-portable.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for p in app.rglob('*'):
            if p.is_file():z.write(p,Path('ArtifactWorkbench')/p.relative_to(app))
    print(json.dumps({'exe':str(app/'ArtifactWorkbench.exe'),'zip':str(archive),'bytes':archive.stat().st_size},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
