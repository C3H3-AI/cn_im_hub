# dsh-im 扫码配置全面对比 + 飞书配置优化方案

> 分析日期：2026-08-18 | 对象：dsh-im（v0.7.1 源码 + @larksuiteoapi/node-sdk 1.73.0）vs cn_im_hub

---

## 1. 各渠道扫码配置对比矩阵

| 渠道 | dsh-im 配置方式 | 底层机制 | cn_im_hub 现状 | cn_im_hub 可复刻？ |
|------|----------------|---------|----------------|-------------------|
| **飞书** | ✅ 扫码创建/绑定机器人（`registerApp`，官方 SDK 设备码流） | `POST accounts.feishu.cn/oauth/v1/app/registration`，`action=begin/poll`；返回 `verification_uri_complete`（二维码）+ `client_id/client_secret` | ❌ 手动填 App ID + App Secret | ✅ **已实测协议，100% 可复刻**（见 §3） |
| QQ | ✅ 手机 QQ 扫码创建机器人（qr-auth.mjs） | 腾讯开放平台扫码 + 回调 | ❌ 手动 AppID + AppSecret | ⚠️ 需逆 qr-auth.mjs 协议 |
| 钉钉 | ✅ 扫码创建机器人（device-auth.mjs） | 钉钉设备码流（`device auth`） | ❌ 手动 Client ID + Secret | ⚠️ 需逆 device-auth.mjs |
| 企业微信 | ✅ 扫码创建智能机器人 | 企微官方扫码 | ❌ 手动 Bot ID + Secret | ⚠️ 需逆 |
| 微信 | ✅ 扫码绑定（iLink 二维码） | 腾讯 iLink `get_bot_qrcode` | ✅ **已有**（WeixinProviderSubentryFlow） | —（已对齐） |
| WhatsApp | ✅ 手机扫码关联设备 | WhatsApp Web 关联设备 | ❌ 无此渠道 | — |
| Slack/Telegram/Discord | 手动 Token（无扫码） | — | — | —（天然无扫码） |

**结论**：dsh-im 的差异化优势 = **飞书/QQ/钉钉/企微四渠道官方扫码创建**；cn_im_hub 目前只有微信扫码。其中**飞书的协议最干净**（官方 SDK 设备码流，无私有签名），与刚做好的 agent_mail 扫码流完全同构，**优先复刻**。

---

## 2. 飞书扫码注册协议（实测确认 2026-08-18）

**端点**：`https://accounts.feishu.cn/oauth/v1/app/registration`（POST，form-urlencoded，无需 UA 特殊处理）

### 阶段 1 — begin（发起）
```
POST .../oauth/v1/app/registration
Content-Type: application/x-www-form-urlencoded
action=begin&archetype=PersonalAgent&auth_method=client_secret&request_user_info=open_id
```
**响应（实测）**：
```json
{
  "device_code": "v1:eyJhbGciOiJFUzI1Ni...（JWT）",
  "user_code": "DRXE-KRH2",
  "verification_uri": "https://open.feishu.cn/page/launcher",
  "verification_uri_complete": "https://open.feishu.cn/page/launcher?user_code=DRXE-KRH2",
  "expires_in": 3600,      // 1 小时（比 agent_mail 的 600s 宽裕）
  "interval": 5            // 轮询间隔（秒）
}
```
- **二维码内容 = `verification_uri_complete`**（用户飞书 App 扫码 → 打开 launcher 页 → 创建/绑定应用）
- 可附加 query 参数定制：`createOnly=true`（只允许新建）、`clientID=<已有app_id>`（更新现有应用）、`appPreset/addons`（预填配置）

### 阶段 2 — poll（轮询）
```
POST 同一端点
action=poll&device_code=<device_code>
```
- 未授权：HTTP 400 + `{error: "authorization_pending"}`（RFC 8628，SDK 注释确认）
- 成功：`{client_id, client_secret, user_info: {...}}`（← 这就是 cn_im_hub 要存的 App ID / App Secret）

### 阶段 3 — 验证（可复用 dsh-im `verifyFeishuApp` 逻辑）
- `POST open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal`（app_id + app_secret）→ tenant_access_token
- `GET open.feishu.cn/open-apis/bot/v3/info/` → 拿 app 名/bot 身份，用于 subentry 标题与健康校验

---

## 3. 飞书扫码配置优化方案（照 agent_mail flow 模式）

**新增 `providers/feishu/flow.py`**（对齐 `providers/agent_mail/flow.py`）：

```
async_step_user
  ├─ POST accounts.feishu.cn begin → device_code / verification_uri_complete / user_code
  ├─ segno 本地生成二维码（内容 = verification_uri_complete）→ 存 _current
  └─ async_step_auth_wait（表单显示二维码 + 授权码 + "用飞书扫一扫"提示）
async_step_auth_wait(提交)
  ├─ 轮询 action=poll（interval 5s，最长 expires_in）
  ├─ 成功 → client_id + client_secret
  ├─ 可选：verifyFeishuApp（tenant token + bot/v3/info）取 app 名做标题
  └─ async_create_entry(data={app_id: client_id, app_secret: client_secret})
async_step_reconfigure
  └─ 保留现有凭据（同 agent_mail）
```

**改动文件**：
| 文件 | 内容 |
|------|------|
| `providers/feishu/flow.py`（新） | 设备码流扫码 flow（copy agent_mail 模式） |
| `providers/feishu/client.py` | `PROVIDER_SPEC.flow_handler` 挂新 flow |
| `translations/en.json` + `zh-Hans.json` | feishu 的 `auth_wait` step 文案 |
| `const.py` | 无需改（app_id/app_secret 字段已存在） |

**保留手动入口**：dsh-im 也是"扫码 + 手动凭据"双入口（README 明确两种按钮）——cn_im_hub 可在 flow 的 `async_step_user` 提供"扫码"与"手动填写"两个选项（vol.Schema 单选），旧用户无缝。

**风险与验证**：
- ✅ begin 端点已实测（零副作用，只拿 device_code）
- ⏳ poll 成功结构（client_id/client_secret）未实测（需要真扫码授权）——按 SDK 类型 `RegisterAppResult` 实现 + 标注
- 二维码有效期 1 小时，体验比 agent_mail（10 分钟）更宽松

---

## 4. 建议落地顺序

1. **飞书扫码 flow**（协议已实测，模式已跑通——本轮直接可做）
2. **QQ / 钉钉 / 企微**：逆向 dsh-im 的 qr-auth / device-auth 协议，评估后逐个复刻（工作量：每个需逆向 + 实测）
3. 微信已对齐，无需动
