# 来源与许可范围

## 当前实现

当前 Python 管理器、Windows 入口、JavaScript 策略与合成测试于 2026-09-30 按接口和行为规范重新编写。实现会话没有接收旧实现、旧测试或上游源码；规范整理者曾审阅旧代码。这是有记录的上下文隔离过程，不是经过法律认证的 clean-room。

项目许可证只适用于贡献者有权授权的新编写部分，不替代第三方许可，也不授予供应商组件的使用或再分发权。

## 早期实现来源

未随本次源码包分发的早期实现曾参考 [callmewenxi/codex-browser-fix](https://github.com/callmewenxi/codex-browser-fix)，核对版本为 `97e5b23600db5d0f8b6ed19cbd62dcbe9f807fd3`。其 MIT 声明保留在 [LICENSE.upstream](LICENSE.upstream)，用于保留历史来源信息。

该早期来源注明移植自 [CodexPlusPlus PR #2208](https://github.com/BigPizzaV3/CodexPlusPlus/pull/2208)，其合并版本 `b5cb05808c95b3992410e930af1e888e5c2c049d` 的项目许可证为 AGPL-3.0。此前未核实到独立授权；不能把直接来源的 MIT 声明当作整个历史授权链已解决的证据。当前新编写部分的授权不以该历史链作为依据，也不将重写描述为消除一切第三方权利风险。

## 供应商组件

兼容性清单包含用于互操作的短调用位置、版本及原件摘要，策略使用公开 Chrome 扩展 ID。完整客户端服务、原件备份和生成的补丁后服务不包含在源码包中。它们仍受各自权利和条款约束。

这是非官方的本地兼容工具，与 OpenAI 无隶属关系。
