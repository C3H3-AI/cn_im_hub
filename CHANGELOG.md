# Changelog

## v2026.08.2 (2026-09-09)

### 🛠 同步上游修复（cherry-pick from ha-china/cn_im_hub）

- **跨平台健壮性批次** — QQ / 钉钉 / 微信 / 企微 / 小艺 / 飞书 客户端健壮性增强：企微踢线检测（disconnect_event 防重连风暴）、应用级心跳、指数退避重连（封顶 5 分钟）、飞书群 @提及占位符清洗、小艺双服务器去重、入站回调异步化
- **企微 subscribe ack 容忍** — 订阅确认超时不再误判为鉴权失败杀连接，仅显式非零 errcode 才算拒绝
- **可观测性** — 企微/小艺 WebSocket 关闭码与原因入日志，异常日志带异常类型名，小艺 TLS 降级降为 debug
- **i18n 架构对齐** — IM 运行时文案迁入 `runtime_translations/`（hassfest 只校验 translations 目录），新增 `i18n.py` 运行时字符串查找
- 冲突解决原则：保留本 fork 的卡片增强、channel_agent_id、重试上限语义；仅采纳与 fork 设计不冲突的修复

### ⏭ 评估后不采纳的上游提交

- 飞书卡片移除系列（35339de/daa0765/f9fadf3）— 与本 fork「全量卡片回复」卖点相反
- 飞书 HTTP webhook 加固（15a90d4）— 本 fork 已统一走 WebSocket 长连接
- 通道标题阻塞 I/O 修复（abc67bd）— 本 fork 改用 const 常量，已无文件 I/O
- CI bilingual changelog（109f587）— 需 fork 仓库配置 LLM secrets

## v2026.07.1 (2026-07-05)

### ✨ 新增

- **按通道配置对话代理** — 支持为每个通道（飞书/微信/企业微信/QQ/钉钉/小艺）单独配置不同的对话代理
- **通道级 agent_id** — 在通道配置中添加 `channel_agent_id` 下拉菜单，可选择 MiMo Auto 或其他对话代理
- **下拉菜单选择** — `channel_agent_id` 使用 `ConversationAgentSelector` 下拉菜单，与全局 `agent_id` 一致

### 🔧 优化

- **架构改进** — 在 `provider_flow.py` 中统一处理 `channel_agent_id` 下拉菜单，避免重复代码
- **向后兼容** — 不配置 `channel_agent_id` 时自动使用全局 `agent_id`

### 使用方法

在任意通道配置中，从下拉菜单选择对话代理：

- **MiMo Auto** — 使用 MiMo Auto 免费通道
- **Claw Assistant** — 使用 Claw Assistant（默认）
- **其他对话代理** — 选择 HA 中配置的其他对话代理

## v2026.06.2

- 初始版本
