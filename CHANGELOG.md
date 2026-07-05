# Changelog

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
