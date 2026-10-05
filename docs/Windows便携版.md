# Windows 便携版与源码构建

普通用户只下载 Release 的 `ArtifactWorkbench-v0.1.4-windows-x64.zip`，完整解压到有写权限的目录，双击 `ArtifactWorkbench.exe` 并允许 Windows 管理员提示。网页会自动打开。不要只复制 EXE。

Windows 包已包含修改版 GOODScanner、内嵌 OCR 模型、离线游戏映射、ONNX Runtime、Python 和所需运行库；兼容用 yas／霜华文件也随包提供。无需另行下载插件、Python、Node、模型或 Agent，也无需手动启动 GOODScanner GUI。扫描、强化和穿戴统一使用包内 GOODScanner，子进程继承主程序权限。支持 Windows x64、1920×1080、简体中文。

## 使用顺序

1. 在“库存导入”选择 GOOD／莫娜 JSON，或进入游戏“背包 → 圣遗物”后扫描。默认扫描当前筛选；扫描全部会清空筛选。
2. 在“角色与预设”填写主属性、评分、暴击计分封顶及圣遗物额外充能下限。
3. 先计算配装，再按需强化或穿戴。霜／尘页只提供建议，不消耗资源。

任务开始时程序会尝试聚焦游戏。运行期间请保持游戏前台；失焦会停止输入并提示处理方法。Win 是中断键，不要用它切回游戏。备用快捷键以网页显示为准。程序永不使用五星圣遗物作强化素材。

配置数据库为 `runtime/ui/workbench.sqlite3`，配装库为 `runtime/loadouts/library.json`。升级前退出旧版并备份整个 runtime，再迁移到新目录。公开 ZIP 不包含 runtime；不要把使用后的整个目录公开上传。

## 开发者构建

以下仅针对源码构建，普通用户无需执行。Release 同时提供主项目源码、GOODScanner 和 yas 对应源码。对应源码包包含已经应用的修改、模型、锁定文件及依赖源码，不要再次打补丁。

GOODScanner 固定上游 `bffc4aad040eac0bb5f10c8b0b2ef86121fb29b5`。解压到 vendor/goodscanner，使用 Rust nightly 和 MSVC C++ 工具链：

```powershell
cargo build --manifest-path vendor/goodscanner/Cargo.toml --release --locked --offline -p genshin_scanner --bin workbench_goodscanner
```

从未经修改的固定上游开始时，使用 tools/patch_goodscanner.py，它会依次应用强化、反馈、换装和失焦保护补丁。yas 对应源码解压到 vendor/yas，编译 yas_artifact 与 yas_readonly。公开二进制通过 --remap-path-prefix 清除本机构建路径。

主程序使用 Python 3.10、requirements.txt 和 PyInstaller 6.22.3：

```powershell
python -m pip install -r requirements.txt
python -m pip install pyinstaller==6.22.3
python tools/build_windows.py --goodscanner-bin vendor/goodscanner/target/release/workbench_goodscanner.exe
```

构建器还需要兼容 yas 产物、bin/cocogoat-control.exe 和 onnxruntime.dll；可用 --yas-bin-dir、--frostflake 指定来源。成品 Windows ZIP 已包含这些文件。构建器不复制 runtime，并拒绝覆盖已有输出目录。

## 验证

`python -m unittest discover -s tests -v` 运行离线测试。游戏验收需显式提供自己的库存/配装，并在允许游戏操作的管理员环境中进行。当前版的实际验收范围及限制见 RELEASE_NOTES.md；不把离线通过表述为所有游戏状态均已实测。
