# Windows 便携版与源码构建

完整解压 Release 的 Windows x64 ZIP 后启动 ArtifactWorkbench.exe。本地端口从 8766 起寻找空闲端口，自动打开正确网页。不要将服务暴露到公网。

游戏功能依赖官方霜华，请从 https://cocogoat.work/extra/client 安装，或把官方程序放到 bin/cocogoat-control.exe。发布包不包含霜华。已保存库存的离线建议无需此组件。

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

生成带依赖目录的程序和 ZIP。需要已构建 yas；可用 --yas-bin-dir 指定其目录。构建不拷贝 runtime，不捆绑霜华。公开分发时必须同时提供适用的许可证和对应 yas 源码。

## 验证与中断

python -m unittest discover -s tests -v 只运行离线单元测试。tools/verify_windows_package.py 用独立副本验证冻结程序；只有显式 --live-scan 才扫描游戏，不强化或换装。

任务运行时左／右 Win 请求停止，备用组合以网页实际注册结果为准，也可使用页面紧急中断。保留未知结果的 pending，不会重复提交消耗。界面适配限定简体中文、1920×1080；从背包圣遗物页开始。
