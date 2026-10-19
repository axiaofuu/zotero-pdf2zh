# 保留本地字体和排版补丁的可选 macOS 启动入口

适用于已有 `server/zotero-pdf2zh-next-venv` 的 macOS + uv 用户。
个人下游补丁在 [BabelDOC 修复分支](https://github.com/axiaofuu/BabelDOC/tree/fix/preserve-lists-code-v0.6.2)，
源码提交为 `4ab2d03e3862803c41c976316adc38145cf17912`。
这不是上游正式版本。NewCM 字体问题的上游修复见 [#622](https://github.com/funstory-ai/BabelDOC/pull/622)。

## 使用

在项目根目录执行：

```sh
zsh automation/start_patched_server.command
```

完整保留启动脚本、`pdf2zh_patch_manager.py` 和 `babeldoc-patches/`。
它们独立于 Server 源码更新目录。默认使用相邻 `../server`；
可用 `PDF2ZH_SERVER_DIR` 指向另一份已有 Server。
保持终端开启，在 Zotero 内选择 `pdf2zh_next` 发起翻译，插件会导入译文附件。
服务只监听 `127.0.0.1:8890`。

## 补丁行为

- NewCM 普通正文字体可翻译，数学字体保持保护；已正确的上游实现无需重复修改。
- 连续、左边界对齐的编号/字母列表拆成独立条目；续行归原条目，编号使用原坐标，正文保留悬挂缩进。
- 多行、至少 95% 等宽字符且多行含代码语法的代码段保留原字符、基线、缩进、空行和颜色，跳过翻译与重排。
- 对布局边界拆出的极短单词尾部，只有字体、字号、基线、XObject 和字符边界连续时才重新拼接。

普通正文软换行继续重排，带行内代码的解释文字仍可翻译。当前结构补丁跳过 OCR workaround 模式，
不覆盖所有列表/代码样式；代码注释保留原文。旧译文需重新翻译才会变化。

## 启动和更新

1. 启动前检查重复运行和 8890 端口。服务运行中不会修改引擎文件。
2. 检查字体和排版补丁。排版目标必须匹配清单中的已审核源码哈希或已知旧补丁哈希；
   未知变化拒绝覆盖。补丁文件本身也核对哈希。修改前备份，文件组写入或行为检查失败会恢复原文件。
3. 检查 PyPI 的兼容正式发布版，遵循 Server 配置、当前 pdf2zh-next 对 BabelDOC 的约束和 Python 版本。
   排除预发布、撤回版本。网络失败时继续当前环境；源码/Fork/直接 URL 安装不会被官方包替换。
4. 有更新时询问是否安装，默认不更新。确认后备份完整环境，再升级、重放全部补丁，
   运行 7 项排版回归、字体分类、依赖一致性和引擎导入检查。失败则恢复原环境及其补丁。
5. 通过 Server 原流程检查官方 Server 更新。更新成功退出后重新运行同一脚本。
   `skip_install=True` 防止 Server 随后自行更新刚检查过的翻译环境。

运行环境保持为官方发布包加本地审核补丁，因此仍能发现官方发布更新。
Fork 保存修复源码；它不会自动合并上游，Sync fork 的 main 也不会改动本修复分支。
官方 0.6.2 与 0.6.4 的段落文件源码相同；完整样例验证使用 0.6.2。
未来未知源码结构或其他兼容问题会使更新回退，不能保证任意未来版本都可直接套用。

## 备份和检查

完整环境备份与失败环境在项目根目录 `.pdf2zh-local/`，保留不自动删除。
macOS 优先文件系统克隆，失败时完整复制。源码文件旁保存带哈希的修改前备份。
仅通过此入口安装的依赖更新经过上述回滚；手动重装、其他入口、Server 源码更新不在该事务内。
强制终止、断电或磁盘故障仍可能需要手动恢复环境。

```sh
zsh automation/start_patched_server.command --check-only
zsh automation/start_patched_server.command --check-updates-only
zsh automation/start_patched_server.command --verify
PYTHONPATH=server python -m unittest discover -s server/tests
```

前两项不修改补丁目标、不启动服务；第二项联网检查发布版本。
`--verify` 在服务停止时修复并验证环境。回归测试模拟安装和写入失败，不需要 API Key、不发送文档。
`babeldoc-patches/LICENSE.BabelDOC` 保留所附 BabelDOC 派生文件的许可。
