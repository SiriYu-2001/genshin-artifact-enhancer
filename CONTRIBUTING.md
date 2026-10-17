# 提交问题与 Pull Request

本仓库接受 Fork 后提交的 Pull Request。不需要仓库写权限，也无需先申请。

请从 `main` 新建分支，将变更提交到自己的 Fork，再向本仓库 `main` 提交 PR。说明出现问题的操作、修改后的行为和验证范围。涉及游戏输入的修改需要分别列出离线测试和实机验收，不要把截图回放等同于全流程成功。

复现问题时，请提供页面底部的版本号、完整错误文字、起始游戏页面和分辨率。阶段强化失败通常需要该次失败目录中的 `frame.png`、`error.json`、`stage-observations.json`。中断与扫描问题需要本次任务的 `stdout.log`，以及是否按了 Win、备用快捷键或网页停止按钮。

不要上传整个 `runtime`、本地数据库、控制器 `server.json` / `session.json` 或令牌。截图可以遮住 UID，但请保持原分辨率和问题区域。公开测试只能使用合成数据；个人回放集留在本地。

程序始终禁止将五星圣遗物用作素材，未知材料也不能确认。修复识别问题不能放宽这些限制。输入可能已生效时必须核对结果，不能重发消耗操作。

Python 回归：`python -m unittest discover -s tests`。前端确定性测试见 `tests/ui_dom_test.mjs`，不需要控制浏览器或游戏。Windows 发行包应在新目录完整解压，使用空用户环境与仅含 Windows 系统目录的 PATH 验证，运行 `tools/verify_offline_package.py`。自检仅操作合成库存，不发送游戏输入。
