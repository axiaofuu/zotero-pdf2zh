# 保留 NewCM 补丁的可选 macOS 启动入口

适用于已经安装 `server/zotero-pdf2zh-next-venv` 的 macOS + uv 用户。
当 BabelDOC 将 NewCM 正文字体误判为公式时，PDF 可能翻译后仍保留英文。
上游修复见 [BabelDOC #622](https://github.com/funstory-ai/BabelDOC/pull/622)，
问题背景见 [#613](https://github.com/funstory-ai/BabelDOC/issues/613)。
此入口在等待上游修复期间保留本地 workaround，现有默认启动方式不变。

## 使用

在项目根目录执行 `zsh automation/start_patched_server.command`，或给该文件执行权限后双击。
脚本与 `pdf2zh_patch_manager.py` 应位于同一目录，独立于 Server 源码更新目录。
默认使用相邻的 `../server`；如需使用另一份已有 Server，可设置 `PDF2ZH_SERVER_DIR`。
保持终端开启，在 Zotero 内选择 `pdf2zh_next` 发起翻译，插件会处理译文附件导入。
服务只监听 `127.0.0.1:8890`。

## 启动与更新

1. 检查实际环境中的字体分类，覆盖 NewCM 正文、数学字体、子集前缀与自定义公式规则。
   分类已正确时不修改；仅对已知的两行错误做备份并修复。
   未知结构或检查失败时停止，交由用户检查。
2. 检查 PyPI 上 pdf2zh-next / BabelDOC 的兼容正式版本，遵循 Server 配置与当前
   pdf2zh-next 的 BabelDOC 依赖约束。排除预发布、撤回和不支持当前 Python 的版本。
   网络失败时打印原因并沿用当前环境。源码、Fork 或直接 URL 安装来源不会被自动替换。
3. 有更新时询问是否安装，默认不更新；无交互输入时也保留当前环境。
   确认后先备份完整虚拟环境，再更新依赖、重放补丁，检查依赖一致性与翻译引擎导入。
   安装或检查失败则恢复旧环境到原路径，保留失败环境用于排查。
4. 通过 Server 原有流程检查官方 Server 更新，仍由用户确认安装。
   Server 更新完成退出后，重新运行本入口。启动时设置 `skip_install=True`，
   避免 Server 再次更新刚刚检查过的翻译依赖。

上游将来修复字体分类后，行为检查通过即使用上游实现，不重复打补丁。
此入口检查发布版本，不自动同步 Git 分支。GitHub Fork 仍需自行 Sync fork 或合并 upstream。

## 备份与限制

依赖更新前的备份及失败环境位于项目根目录 `.pdf2zh-local/`，保留不自动删除；
确认无需回退且服务已停止后可手动清理。macOS 优先使用文件系统克隆，无法克隆时完整复制，
因此需要足够磁盘空间。字体文件旁另保存带内容哈希的原文件备份。

锁与端口检查阻止重复启动及正在运行时通过此入口升级。其他入口或手动安装不经过本回滚流程，
更新时应关闭其他 Server。回滚覆盖翻译虚拟环境，不覆盖 Server 源码更新。
强制终止进程、断电或磁盘故障仍可能需要从 `.pdf2zh-local/` 手动恢复环境到原路径。
字体检查与引擎导入不能证明未来每个版本都能正确翻译每份 PDF；更新后仍应检查译文。

## 检查命令

```sh
zsh automation/start_patched_server.command --check-only
zsh automation/start_patched_server.command --check-updates-only
PYTHONPATH=server python -m unittest discover -s server/tests -p test_local_patch_manager.py
```

前两条不修改字体文件，不启动服务；第二条会联网检查发布版本。
测试使用临时环境与模拟安装失败，无需 API key，不发送文档。
