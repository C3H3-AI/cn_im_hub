# cn_im_hub 小艺通信深度分析

**日期**: 2026-06-12
**分析范围**: `providers/xiaoyi/client.py`, `const.py`, `core/command.py`, `core/conversation.py`

---

## 一、架构定位

小艺（XiaoYi）是 cn_im_hub 七个 provider 中唯一一个**不是传统 IM 机器人**的通道。它实现的是**A2A（Agent-to-Agent）反向通道**——通过双 WebSocket 连接将 HA 接入小艺智能体平台，使小艺云端智能体能直接与 HA 对话。

与飞书/企微/钉钉/QQ/微信的 **用户→bot→HA** 路径不同，小艺的路径是 **用户→小艺智能体平台→WebSocket→HA**。

## 二、双 WebSocket 容灾连接

**两个服务器地址（const.py）：**
- `server1`: `wss://hag.cloud.huawei.com/openclaw/v1/ws/link` — 云端主节点
- `server2`: `wss://116.63.174.231/openclaw/v1/ws/link` — IP 直连备用

**连接策略：**
- `start()` 用 `asyncio.gather` **同时并发连接**两个服务器
- 任一服务器处于 `ready` 状态即认为整体 connected
- 两个全挂才抛 `RuntimeError`
- 断线后指数退避重连（2^k 秒，上限 60s，最多 8 次）
- 稳定连接 30 秒后重置重连计数器

**SSL 处理：**
- 域名端点（server1）→ 标准 SSL/TLS
- IP 端点（server2）→ `CERT_NONE` + `check_hostname=False`（IP 证书无 SAN）

## 三、身份认证（HMAC-SHA256）

```python
def _build_auth_headers(ak, sk, agent_id):
    timestamp = ms_timestamp()
    digest = hmac.new(sk, timestamp, sha256).digest()
    return {
        "x-access-key": ak,
        "x-sign": base64(digest),
        "x-ts": timestamp,
        "x-agent-id": agent_id,
    }
```

连接成功后立即发送 `clawd_bot_init` 进行协议级初始化。

## 四、A2A 协议（JSON-RPC 2.0 + 自定义封装）

### 外层帧结构
```json
{
  "msgType": "agent_response",
  "agentId": "...",
  "sessionId": "...",
  "taskId": "...",
  "msgDetail": "<JSON-RPC 2.0 序列化字符串>"
}
```

### 入站消息（小艺→HA）
```json
{
  "method": "message/stream",
  "id": "task_id",
  "agentId": "...",
  "params": {
    "sessionId": "...",
    "message": {
      "parts": [{"kind": "text", "text": "用户消息"}]
    }
  }
}
```

### 出站回复（HA→小艺）— artifact-update 增量流
```json
{
  "taskId": "...",
  "kind": "artifact-update",
  "append": true,
  "lastChunk": false,
  "final": false,
  "artifact": {
    "artifactId": "artifact_<uuid>",
    "parts": [{"kind": "text", "text": "回复内容"}]
  }
}
```

三种结束状态：
- **正常完成**: `lastChunk=true, final=true`
- **取消**: `kind=status-update, state=canceled`
- **错误**: JSON-RPC error 响应

## 五、消息路由全链路

```
鸿蒙设备 / 小艺 App
   ↓ A2A WebSocket
小艺云端智能体平台
   ↓ 同上双 WS
XiaoYiClient._listen_server()
   ↓ _handle_message() — 按 method 分派
     ├─ clearContext → 回复 cleared
     ├─ tasks/cancel → 取消_active_prompts中对应task
     └─ message/stream → _process_prompt()
           ↓ 加前缀 [IM:XiaoYi user=session_id]
           ↓ parse_command() → conversation command
           ↓ execute_command()  → ask_home_assistant()
                 ↓ conversation.async_converse()
                 ↓ 或 conversation.process 服务
           HA Conversation Agent (AI Hub / 自定义)
                 ↓
           回复文本
           ↓ _send_text_chunk() + _send_final()
```

**关键细节：** `session_servers` dict 做黏性路由，每个 session 绑定到首次到达的 server，后续出站消息都走同一连接。

## 六、Live Progress（实时进度推送）

这是小艺独有的**流式进度**机制：

1. `_run_live_progress_bridge` 是一个 `asyncio.Task`
2. 订阅 HA 事件总线 `ha_crack_live_progress` 事件
3. 事件 payload 中匹配 `conversation_id == f"xiaoyi:{session_id}"`
4. 匹配时通过 `_send_text_chunk` 即时将进度推送给用户
5. 进度文本格式化：`display_text` 去特殊字符 → 纯文本，或 `tool_name`

**效果：** 用户在鸿蒙设备上跟小艺说"打开客厅灯"，过程中能实时看到 "正在查询设备…" → "正在执行操作…" 等中间状态。

**实现方式：** `asyncio.Queue` + HA `callback` 监听器 + `async def _fire_and_forget()` 发送。

## 七、连接生命周期管理

| 机制 | 参数 | 说明 |
|------|------|------|
| App Heartbeat | 间隔 20s | 定时发 `{"msgType":"heartbeat"}` |
| Watchdog | `_WATCHDOG_TIMEOUT=0`（默认禁用）| 可配置开启，超时强制关闭 WS |
| 断线重连 | 指数退避 2^k → 上限 60s | 最多重试 8 次（`_MAX_RECONNECT_ATTEMPTS`） |
| 稳定检测 | `_STABLE_CONNECTION_THRESHOLD=30s` | 30s 后重置重连计数 |

## 八、send_text 出站路径

小艺的 `send_text` 只能推送给**已有活跃会话**的用户（`target_type="session_id"`），因为 `session_servers` 映射只在入站消息到达时被动建立。

流程：HA 侧调用 `cn_im_hub.send_message` 服务 → 按 channel 路由 → 查 `session_servers` 找到对应 WS → 构造新 task → 发 chunk + final。

## 九、能力范围

在 `ProviderRuntime` 中，小艺只实现了：
- `send_text`（文本）
- 能力等级：`basic`（1/9 种）
- 无图片、视频、文件、语音、TTS、审批、卡片出站能力

## 十、拓扑全景

```
鸿蒙设备 / 小艺App
     ↕ A2A WebSocket (wss://hag.cloud.huawei.com + IP直连)
小艺云端智能体平台
     ↕ 同上双WS
XiaoYiClient
     ↕ conversation.async_converse
HA Conversation Agent
     ↕
HA 服务 / 设备控制
```

## 十一、与 MEMORY.md 的关联

此前在 `MEMORY.md` 中记录的"小艺 Push 功能"相关背景吻合：`send_text()` 走 A2A `artifact-update` 需要活跃会话，主动推送被云端静默丢弃的问题即源于此协议约束。解决方案 `send_push()` 走 HTTP Webhook 实际是绕过了本协议的局限性。

## 十二、关键文件索引

| 文件 | 作用 |
|------|------|
| `providers/xiaoyi/client.py` | XiaoYiClient 核心实现（~400 行） |
| `providers/xiaoyi/__init__.py` | 导出 PROVIDER_SPEC |
| `const.py` | 常量定义（URL、配置键） |
| `core/command.py` | parse_command + execute_command |
| `core/conversation.py` | ask_home_assistant（HA 对话 API 桥接） |
| `core/known_targets.py` | KnownTargetTracker（session 持久化） |
| `core/service.py` | send_message 服务 handler |
