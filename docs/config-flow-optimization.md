# cn_im_hub 配置方式优化建议

> 分析日期：2026-08-18 | 聚焦：ConfigFlow / subentry 配置流 / 凭据生命周期
> 对象：`provider_flow.py`、`__init__.py`、`providers/registry.py`、各 provider schema、`translations/*.json`

---

## 1. 现有配置机制

```
主 ConfigFlow（选全局 agent_id）
   └── subentry 配置流（每通道一个）
         ├─ async_step_user      → 检查 allow_multiple / max 3
         ├─ async_step_set_options → 表单 = spec.schema_builder() + channel_agent_id 下拉
         │                          → validate_config()（真实网络校验）→ _complete()
         └─ async_step_reconfigure → 回填旧值修改
_complete: 创建/更新 subentry + async_reload(整个 entry)
```

---

## 2. 优化建议

### 🔴 P0 — 正确性问题（必改）

| # | 问题 | 影响 | 方案 |
|---|------|------|------|
| 1 | **agent_mail `refresh_token` 轮换不落盘** | 后端 refresh_token **每次刷新轮换**（协议确认）；client `_refresh()` 只改内存 `self._refresh_token`，从不写回 subentry。**重启后旧 refresh_token 已失效 → 401 → 通道永久挂**，用户只能删了重配 | client 持有一个 `on_tokens_refreshed(access, refresh)` 回调（由 setup_provider 注入）；刷新成功后调 `hass.config_entries.async_update_subentry(entry, subentry_id, data={**旧, "access_token":新, "refresh_token":新})` 落盘。QQ 等有 TTL 的 provider 同理（token 过期重获取后也应回写） |
| 2 | **改一个通道 = 全量 reload 所有通道** | `_complete()` 里 `async_reload(entry.entry_id)` 把整个集成重载——改 QQ 会把微信/飞书/agent_mail 连接全断一遍再重连 | 短期：文档注明该行为；中期：评估每通道独立 ConfigEntry（每通道一个 entry，改一个只 reload 一个）——若保留"统一 Hub"设计则接受全量 reload 并记录限制；至少把重载失败/耗时暴露到日志 |

### 🟠 P1 — 配置体验（高价值）

| # | 问题 | 现状 | 方案 |
|---|------|------|------|
| 3 | **agent_mail 配置要先在终端跑 CLI 拿 token 再粘贴** | 依赖外部步骤（`agently-cli auth login` + 微信扫码），用户要会命令行 | **内置设备码流向导**：`async_step_user` → 调 `/oauth/device?func=1` → 表单页显示授权链接/二维码 + `input_code` → "我已授权"按钮 → 轮询 `func=2` 拿 token → 自动完成。**`ProviderSpec.flow_handler` 机制已有且 wechat 已在用**（`WeixinProviderSubentryFlow`），agent_mail 照此模式即可。协议细节已在 `API_CONTRACT.md` 100% 确认 |
| 4 | **敏感字段 reconfigure 回显明文** | `_current_data()` 把旧 token 回填进表单 `default`，页面显示 token 值 | 敏感字段（access_token/secret）reconfigure 时 `default=""` + 提示"留空表示保持不变"；提交时 `旧值 if 空 else 新值` |
| 5 | **错误文案笼统** | 任何验证失败都 `errors["base"]="cannot_connect"` | validate_config 抛自定义异常类型（`InvalidTokenError` / `TimeoutError` / `NotAuthorizedError`），provider_flow 映射到 `errors["base"]="invalid_token" / "timeout" / "not_authorized"`，translations 补文案——401（token 无效）和网络超时是两种完全不同的用户动作 |

### 🟡 P2 — 工程细节

| # | 建议 | 说明 |
|---|------|------|
| 6 | `_MAX_INSTANCES_PER_PROVIDER=3` 硬编码 | 移到 `ProviderSpec.max_instances`（agent_mail 已 allow_multiple=False，无需改） |
| 7 | 表单敏感输入不可见 | HA ConfigFlow 支持 `vol.Required(..., description={"suggested_value": ...})`；敏感字段可用 password 类型（`str` + `cv.matches_regex` 不解决遮挡，需要自定义 `selector` 或用 `password` input——HA 无内置，可接受现状，注明） |
| 8 | `flow._get_entry()` / `_get_reconfigure_subentry()` 是 HA 私有 API | 版本升级有风险；封装一个兼容层或关注 HA 是否提供公开 API |
| 9 | 配置完成后无引导 | agent_mail 完成页可提示"发一封测试信到你的邮箱验证"；QQ 提示"主动打招呼触发入站"——描述已有部分承担，可加 `step` 级 hint |
| 10 | token 有效期展示 | agent_mail `auth status` 有 `expires_at`——配置页/状态页可显示 access_token 剩余有效期，接近过期给提示（同 dsh-im 建议） |

---

## 3. 落地顺序

1. **P0-1**：token 轮换落盘（5 分钟改动，防止重启即挂——agent_mail 上线前必做）
2. **P1-3**：agent_mail 设备码流向导（复用 flow_handler，把"粘贴 token"升级为"扫码授权"）
3. **P1-5**：错误文案细分（改 provider_flow + translations）
4. **P1-4 / P0-2**：敏感字段留空保留 → reload 策略
5. P2 按需
