# Codex Chrome API-key Fix

Windows 上的非官方本地工具，用于缓解 Codex 调用 Chrome 时出现的 `unsupported Codex auth method: apikey` 错误。它为浏览器服务加入请求标识策略，不更改 API key、代理配置或原生登录逻辑。

> 本项目由 **GPT-6 Astra** 提供 AI 辅助开发支持，包括代码编写、审查与测试；不代表 OpenAI 官方背书。

仅在浏览器插件 **26.924.51851**、客户端 **26.924.6891.0** 上验证过。其他版本可以尝试，但不保证能正确运行，也不保证适用于所有同名错误。

## 使用

需要 Windows PowerShell 5.1、Python 3.10+，以及已安装并连接的 ChatGPT Chrome 扩展。Node 使用客户端配置中的版本，无需另装依赖。

下载并解压源码，在目录中打开终端，确认客户端没有正在更新，然后执行：

```powershell
.\patch.cmd status
.\patch.cmd install
```

安装后重启客户端，再测试 Chrome。默认命令是只读 `status`。未验证的插件版本不会仅因版本号不同而被拒绝，输出会标记 `versionValidation: "unverified"` 并附带提示；工具会尝试沿用已有补丁规则。

仍需唯一匹配已知修改位置，安装前生成的代码必须通过 Node 语法检查；这些检查不保证运行时兼容。如果修改位置变化、匹配不唯一、已安装文件被修改或备份损坏，工具会拒绝相关操作。已验证版本仍保留原件摘要校验，避免覆盖该版本的意外改动。

其他操作：

```powershell
.\patch.cmd disable    # 暂停本地策略
.\patch.cmd enable     # 重新启用完整安装
.\patch.cmd uninstall  # 校验后恢复原件，保留备份
```

卸载后也需重启客户端，无须重装。官方更新可能覆盖补丁；更新后先检查状态，再尝试重新安装。若新版本的代码结构不匹配，需要调整补丁规则。不要在客户端更新期间安装或卸载。

## 路径和常见问题

- Python 自动从已激活环境、`py -3`、PATH 和已有 conda 安装中查找；也可用 `CHROME_COMPAT_PYTHON` 指定解释器完整路径。
- 配置默认读取 `CODEX_HOME` 或当前用户的 `.codex`，可用 `--codex-home` 指定其他绝对目录。
- 开关和备份默认放在 `%LOCALAPPDATA%\ChromeCompatPatch`，可用 `--state-dir` 或 `CHROME_COMPAT_STATE_DIR` 指定。移动代码目录不影响它们；不要直接搬动状态目录。
- `migration-required`：旧 schema 2 安装可先 `uninstall` 再 `install`；更早的仓库内 schema 1 需用对应旧工具恢复。
- `conflict`、`backup-invalid`：保留现场，核对改动和备份，不要强制覆盖。`locked` 表示存在操作锁，先确认没有补丁进程仍在运行再处理。
- `incompatible-structure`：无法唯一匹配已有补丁规则，需要适配当前插件的代码结构。
- 配置解析仅支持常见的单行 TOML 字符串；不支持的语法会拒绝。`runtimeLoaded: "not-checked"` 表示磁盘检查不能证明服务已重新加载。

## 隐私和范围

工具不上传配置或凭据。启用后，受控网页请求可能携带 `x-browser-agent: ChatGPT/<session-id>`，网站可能据此识别代理访问。停用或卸载不会承诺清除扩展已保存的标识状态。

诊断输出可能包含本机路径，分享前请脱敏。不要上传 `state/`、`temp/`、登录配置或完整客户端服务备份。

## 测试与许可

测试只使用合成服务。用 Python 3.10+ 执行 `-B -m unittest discover -s tests -v`；测试所需 Node 从 PATH 或 `CHROME_COMPAT_TEST_NODE` 获取。此前合成测试和上述版本的本机 Chrome 调用已通过，其他版本仍需实际验证。

新编写且贡献者有权授权的部分采用 [MIT](LICENSE)。重写过程、历史来源和供应商代码范围见 [NOTICE.md](NOTICE.md)。这是按现状提供的小工具，不承诺持续适配客户端更新。
