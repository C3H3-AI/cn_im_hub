# cn_im_hub Ultra

把常见即时通信平台聚合到一个 Home Assistant 集成中。  
Aggregate common Chinese IM platforms into one Home Assistant integration.

> 安装前请确保在 HACS 中添加的自定义仓库地址为：`https://github.com/C3H3-AI/cn_im_hub`

告别复杂的 Node-RED 工作流，告别容易出错的 YAML 编排。配合已收录在 HACS 中的 `AI HUB`，你可以直接用自然语言控制家里的设备，全部能力都在 Home Assistant 内完成。

[![Open your Home Assistant instance and open AI HUB in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ha-china&repository=ai_hub&category=integration)

---

## 🚀 与上游的差别 / Differences from Upstream

本仓库 fork 自 [ha-china/cn_im_hub](https://github.com/ha-china/cn_im_hub)，在此基础上增加以下特有功能：

| 功能 | 本仓库 | 上游 |
|------|--------|------|
| **按通道配置对话代理** | ✅ 每个通道可独立选择不同的 `agent_id` | ❌ 仅支持单一全局 `agent_id` |
| **飞书全量卡片回复** | ✅ AI 回复全部以飞书卡片展示 | ❌ 仅长内容自动转卡片 |
| **智能卡片路由** | ✅ `from_ai` 标记路由，AI 与非 AI 回调互不干扰 | ❌ 无此功能 |
| **WebSocket + Webhook 双重回调** | ✅ 飞书长连接与 HTTP 端点均支持卡片回调 | ❌ 无此功能 |
| **回调加密验证** | ✅ 可选 `verification_token` 校验飞书签名 | ❌ 无此功能 |
| **`card_json` 参数** | ✅ `send_message` 支持传入卡片 JSON | ❌ 无此参数 |
| **简单格式卡片** | ✅ 支持 `card_content` + `card_buttons` 无需写 JSON | ❌ 无此功能 |
| **卡片构建器** | ✅ Lovelace 自定义卡片，可视化构建，实时预览 | ❌ 无此功能 |
| **MIT License** | ✅ 已添加 | ❌ 原仓库无 LICENSE |

> 💡 **保持同步**：本仓库定期合并上游更新，确保基础功能与上游一致。

---

## 文档 / Docs

- 中文配置指南：[`CONFIG.zh-CN.md`](CONFIG.zh-CN.md)
- Chinese setup guide for official platform backends: [`CONFIG.zh-CN.md`](CONFIG.zh-CN.md)

## 当前支持 / Supported Providers

- Feishu
- WeCom
- QQ（WebSocket 网关） / QQ (WebSocket gateway)
- DingTalk（Stream 模式） / DingTalk (Stream mode)
- WeChat（个人微信，支持多人绑定） / WeChat personal accounts with multi-binding
- XiaoYi（小艺 A2A WebSocket） / XiaoYi A2A WebSocket

## Ultra 增强功能 / Ultra Features

- **按通道配置对话代理**：每个通道（飞书/微信/企业微信/QQ/钉钉/小艺）可单独选择不同的对话代理，不配置时自动使用全局 `agent_id`  
  **Per-channel agent override**: each provider channel can independently select a different conversation agent; falls back to the global `agent_id` when not configured.
- **全量卡片回复**：AI 回复全部以飞书卡片形式展示，不再混杂纯文本  
  **Full card replies**: all AI responses are displayed as Feishu interactive cards.
- **智能卡片路由**：Claw AI 发出的卡片按钮回执自动路由回 AI 处理，非 AI 来源的按钮回调触发标准 HA 事件，互不干扰  
  **Smart card routing**: card button callbacks from Claw AI are automatically routed back to the AI; non-AI callbacks (e.g. doorbell) fire standard HA events.
- **WebSocket + Webhook 双重卡片回调**：飞书长连接`card.action.trigger`事件与 HTTP Webhook 端点均支持 `from_ai` 过滤，AI 卡片不泄漏到自动化  
  **Dual callback handling**: both WebSocket `card.action.trigger` events and the HTTP Webhook endpoint filter by `from_ai` flag.
- **回调加密验证**：可选配置 `verification_token`，webhook 端点自动校验飞书回调签名，防止伪造请求  
  **Callback security**: optional `verification_token` authenticates Feishu card callback requests.

## 基础功能 / Core Features

- 一个 Hub 统一接入多个 IM 平台  
  One Hub can connect multiple IM providers.
- 集成级配置全局 `agent_id`，各通道可单独覆盖  
  Configure a global `agent_id` at integration level, with per-channel override support.
- 各平台通过 subentry 独立添加、独立更新，并可选择各自的对话代理  
  Each provider is managed as an independent subentry, with its own agent selection.
- 个人微信支持绑定多个账号  
  Personal WeChat supports multiple bound accounts.
- 统一的 `cn_im_hub.send_message` 服务  
  Unified `cn_im_hub.send_message` service.
- `camera_entity` 可直接抓拍并发送图片  
  `camera_entity` can capture and send snapshots directly.
- 图片出站当前支持 `WeChat`、`WeCom`、`Feishu`、`QQ`、`DingTalk`  
  Outbound image sending currently supports WeChat, WeCom, Feishu, QQ, and DingTalk.
- 语音只在平台已提供识别文本时转给 HA  
  Voice is passed to HA only when the platform already provides transcript text.
- 飞书卡片 + 摄像头截图：`send_message` 同时传入 `card_json` 和 `camera_entity` 时，自动抓拍并嵌入卡片  
  Feishu card + camera snapshot: when `card_json` and `camera_entity` are both provided, the camera snapshot is automatically captured and injected into the card.
- 飞书卡片回调端点 `/api/cn_im_hub/feishu/card_callback`  
  Feishu card callback endpoint `/api/cn_im_hub/feishu/card_callback`.

## 飞书卡片构建器 / Feishu Card Builder

可视化构建飞书交互卡片，无需手写 JSON。支持标题、Markdown 正文、多行按钮，带实时预览。

### 安装

1. 将 [`www/cn-im-hub-card-builder.js`](www/cn-im-hub-card-builder.js) 复制到 HA 的 `config/www/` 目录下。
2. 进入 HA → 设置 → 仪表盘 → 右上角三点 → 资源管理 → 添加资源 → `/local/cn-im-hub-card-builder.js`，类型选 JavaScript 模块。
3. 在仪表盘编辑模式下点击添加卡片，搜索"飞书卡片构建器"。
4. 填写卡片内容 → 点击 **复制 JSON** → 在开发者工具中粘贴到 `card_json` 字段。

### 按钮格式说明

| 字段 | 操作 |
|------|------|
| 标签 | 按钮上显示的文字 |
| 值 | 按钮点击时传递的数据 |
| 颜色 | 下拉选择（蓝/红/灰/强调/绿） |
| 行 | [+ 添加行] 创建新行 |
| 删除 | 每个按钮和行都有 ✕ 删除按钮 |

### 效果

左侧编辑面板，右侧实时预览卡片效果，复制 JSON 或直接调用 `send_message` 发送。

## 安装 / Installation

1. 将本仓库部署到 HA 的 `custom_components/cn_im_hub`。  
   Deploy this repository to `custom_components/cn_im_hub`.
2. 重启 Home Assistant。  
   Restart Home Assistant.
3. 进入 `设置 -> 设备与服务 -> 添加集成`，搜索 `cn_im_hub Ultra`。  
   Go to `Settings -> Devices & Services -> Add Integration`, then search for `cn_im_hub Ultra`.
4. 首次添加时选择全局 `agent_id`。  
   Select the global `agent_id` during first setup.
5. 然后按平台添加子服务，每个通道可选择自己的对话代理（不配置则使用全局 `agent_id`）。后台配置步骤见 [`CONFIG.zh-CN.md`](CONFIG.zh-CN.md)。  
   Then add provider subentries — each channel can optionally select its own agent (falls back to global `agent_id`). Backend setup steps are documented in [`CONFIG.zh-CN.md`](CONFIG.zh-CN.md).

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=C3H3-AI&repository=cn_im_hub&category=integration)

## HA 服务 / HA Service

- `cn_im_hub.send_message`
- 参数 / Fields:
  - `channel`
  - `target`
  - `message`
  - `camera_entity`
  - `card_json`（飞书卡片 JSON，与 `camera_entity` 组合可自动嵌入截图） / Feishu card JSON; combined with `camera_entity` auto-injects snapshot
  - `card_title`（卡片标题，与 card_content/card_buttons 搭配使用） / Card header title, used with card_content/card_buttons
  - `card_content`（卡片正文，替代手写 JSON 的可视化方式） / Card body text, a visual alternative to writing JSON
  - `card_buttons`（卡片按钮，简单格式，无需 JSON。`|` 分行，`,` 分按钮，`=` 分标签/值，`@` 指定颜色。例：`确认=yes@blue, 取消=no@red | 下一页=next`） / Buttons in simple format without JSON
  - `wechat_account_id`（仅多微信账号时可选） / optional for multi-WeChat routing

## 目标地址格式 / Target Routing

- `channel` 选择发送通道与目标类型，例如：`feishu/chat_id`、`qq/group`、`wechat/user_id`。  
  `channel` selects the provider and target type, for example `feishu/chat_id`, `qq/group`, `wechat/user_id`.
- 如果存在多个同类平台实例，`send_message` 会先按 `target` 命中历史目标自动路由；如果没填 `target`，则自动使用当前唯一已选的 `target selector`。  
  If multiple instances of the same provider exist, `send_message` first routes by a known `target`; if `target` is empty, it falls back to the only currently selected `target selector`.
- 多个个人微信账号并存时，通常无需手填 `wechat_account_id`；仅在路由仍然歧义时才需要填写。  
  With multiple personal WeChat accounts, `wechat_account_id` is usually not required unless routing is still ambiguous.
- `camera_entity` 会抓取当前快照并作为图片发送。  
  `camera_entity` captures the current snapshot and sends it as an image.

## 对话方式 / Conversation Flow

- 每个通道的消息默认转到全局 `agent_id` 对应的 HA conversation agent；若通道配置了 `channel_agent_id`，则使用该通道专属代理。  
  Messages are forwarded to the global `agent_id` by default; if a channel has `channel_agent_id` configured, its dedicated agent is used instead.
- 以自然语言对话为主。  
  Natural-language conversation is the main interaction style.
- 飞书渠道支持实时进度推送（需在子服务配置中开启）。  
  Feishu supports live progress push during AI processing (enable in subentry config).

## 卡片回调路由 / Card Callback Routing

```
Claw AI 发卡片 → 按钮 value 带 from_ai 标记
    ↓
用户点按钮 → WebSocket 或 Webhook 收到回调
    ↓
判断 from_ai = true?
  ├─ 是 → 自动路由回 AI，AI 回复以新卡片发回群中
  └─ 否 → 触发 cn_im_hub_feishu_card_action 事件，供 HA 自动化消费
```

## 参考 / References

- 平台后台配置与截图：[`CONFIG.zh-CN.md`](CONFIG.zh-CN.md)
- Upstream tracking: [`upstream.txt`](upstream.txt)
