# Windows 便携版与源码构建

完整解压 Release 的 Windows x64 ZIP 后双击 ArtifactWorkbench.exe，Windows 会请求管理员授权。本地端口从 8766 起寻找空闲端口，自动打开正确网页。不要将服务暴露到公网。

Windows ZIP 自带霜华、yas 与 ONNX Runtime。无需自行安装或启动插件；控制器会在管理员权限下自动启动。点击扫描后程序会自动切回游戏，不要按 Win 中断键切换窗口。

配置、库存、配装、截图和任务记录保存于 runtime。升级保留该目录。数据管理页可删除、恢复或彻底删除配置；历史最多保留 20 版。正常截图在网页工作任务结束后清理，错误和未决动作的证据保留。

## 构建适配版 yas

本项目固定上游 https://github.com/1803233552/yas 的提交 `614245fde088667216b80ff2133a7a44ebeb4d1f`。Release 同时提供包含实际修改、模型、Cargo.lock 和 Rust 依赖源码的 `yas-corresponding-source.zip`，不带 Git 历史。解压其中 yas 到本项目 vendor/yas，不要再次打补丁。使用 Rust stable、MSVC C++ 工具链：

```powershell
cd vendor/yas
cargo build --release --locked -p yas-application --bin yas_artifact
cargo build --release --locked -p yas_scanner_genshin --bin yas_readonly
```

也可自行获取固定上游及 LFS 模型后运行 tools/patch_yas.py；与发行二进制严格对应时优先使用 Release 源码包。重分发时使用 Rust --remap-path-prefix 清除本机用户名及构建目录。

## 构建主程序

Python 3.10，安装 requirements.txt 后：

```powershell
python -m pip install pyinstaller==6.22.3
python tools/build_windows.py
```

生成带依赖目录的程序和 ZIP。需要已构建 yas；可用 --yas-bin-dir 指定其目录。构建不拷贝 runtime，同时打包 bin/cocogoat-control.exe（可用 --frostflake 指定来源）。公开分发时必须同时提供适用的许可证和对应 yas 源码。

## 验证与中断

python -m unittest discover -s tests -v 只运行离线单元测试。tools/verify_windows_package.py 用独立副本验证冻结程序；只有显式 --live-scan 才扫描游戏，不强化或换装。

任务运行时左／右 Win 请求停止，备用组合以网页实际注册结果为准，也可使用页面紧急中断。保留未知结果的 pending，不会重复提交消耗。界面适配限定简体中文、1920×1080；从背包圣遗物页开始。

## 隔离验收脚本

`tools/verify_release_scan.py --archive <ZIP>` 从新的解压目录经网页 API 发起扫描。`tools/verify_release_equip.py` 接受显式指定的配装库、历史扫描目录、配装 ID 和备用花索引，在新目录测试临时换装、恢复方案及幂等复核。这些开发验收脚本需要从管理员终端运行 Python；被测试的发行程序不需要。测试数据与凭据仅写入忽略的 runtime，不随发行包公开。用户中断会记为未完成，不能当作完整流程通过。

管理员启动后可用 `ArtifactWorkbench.exe controller-check` 检查包内控制器连接，结果在 runtime/controller-check.json；此命令不操作游戏。
