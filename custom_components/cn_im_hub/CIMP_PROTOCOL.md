# CIMP - CN IM Protocol 技术方案 v1.0

## 1. 背景与问题

### 现状痛点
| 问题 | 表现 |
|------|------|
| 无结构化协议 | AI<->IM Hub 之间只有纯文本 + [IMAGE:xxx] 标签嵌入，无法表达卡片/按钮回调/状态更新 |
| 卡片回调断链 | 飞书卡片按钮点了只弹 toast，不会回灌给 AI，用户等于白点 |
| AI 无渠道感知 | AI 不知道对面是飞书(支持卡片)还是企业微信(纯文本)，只能无差别输出 |
| 回复格式硬编码 | 飞书 provider 强制包卡片，AI 没有选择权 |
| 无法跨对话回复 | AI 只能回复原聊天，无法主动推送到其他渠道 |

### 设计目标
1. **结构化通信** - AI 输出结构化帧(JSON Lines)，IM Hub 解析后按渠道能力渲染
2. **能力协商** - IM Hub 向 AI 声明渠道能力，AI 按能力输出合适的帧
3. **双向交互** - 卡片按钮回调重构为帧喂给 AI，保持 conversation_id 连续
4. **向下兼容** - 现有纯文本回复和 [IMAGE:xxx] 标签语法仍然可用
5. **渠道无关** - 协议不绑定飞书/微信/QQ，任何 IM 渠道都能适配

---

## 2. 协议帧定义

### 2.1 帧格式
每帧为独立 JSON 行(JSON Lines)，AI 回复可以输出多帧：

```
{"type":"text","content":"好的，正在处理"}
{"type":"card","scene":"confirm","title":"确认操作","body":"确定要打开灯吗？","buttons":[{"id":"yes","label":"确认","style":"primary"},{"id":"no","label":"取消","style":"default"}]}
{"type":"media","kind":"image","source":"camera.living_room"}
{"type":"voice","text":"客厅灯已经打开了"}
{"type":"state_update","card_id":"xxx","fields":{"status":"done"}}
{"type":"redirect","target":"feishu:oc_xxx","content":"门口有人按铃"}
```

### 2.2 帧类型

#### text - 纯文本
```json
{"type":"text","content":"回复的文本内容","conversation_id":"可选"}
```

#### card - 交互卡片
```json
{
  "type": "card",
  "scene": "default|control|alert|confirm|approval",
  "title": "卡片标题",
  "body": "卡片正文",
  "buttons": [
    {"id": "yes", "label": "确认", "style": "primary"},
    {"id": "no", "label": "取消", "style": "default"}
  ],
  "conversation_id": "feishu:oc_xxx"
}
```
- scene 决定颜色: default=蓝, control=绿, alert=红, confirm=橙, approval=紫
- buttons 为空时纯展示；按钮点击以 button_click 帧回灌

#### media - 媒体消息
```json
{"type":"media","kind":"image|video|gif|file|audio","source":"camera.xxx|/path|url","file_name":"可选","caption":"可选"}
```

#### voice - 语音
```json
{"type":"voice","text":"要转语音的文字"}
```

#### capabilities - 能力声明(IM Hub -> AI)
```json
{"type":"capabilities","channel":"feishu","supports":["text","card","image","buttons"],"max_text_length":1800,"conversation_id":"feishu:oc_xxx"}
```
每次对话开始时，IM Hub 先发 capabilities 帧给 AI。

#### button_click - 按钮回调(IM Hub -> AI)
```json
{"type":"button_click","button_id":"yes","card_id":"原卡片标识","button_label":"确认","conversation_id":"feishu:oc_xxx","chat_id":"oc_xxx","user_id":"可选"}
```
用户点击卡片按钮后，IM Hub 将此帧喂给 AI。

#### state_update - 卡片状态更新
```json
{"type":"state_update","card_id":"消息ID","fields":{"status":"已处理","button_1":"disabled"}}
```

#### redirect - 跨对话转发
```json
{"type":"redirect","target":"feishu:oc_xxx","content":"门口有人按铃","media":{"type":"media","kind":"image","source":"camera.door"}}
```

#### error - 错误帧
```json
{"type":"error","code":"INVALID_CARD","message":"卡片格式错误"}
```

---

## 3. 帧处理管道

### 3.1 整体架构

```
+------------------------------------------------------------------+
|                        AI (conversation agent)                    |
|  输入: [IM:Feishu|...] + capabilities帧 + user_text              |
|  输出: JSON Lines 帧 (text | card | media | voice | ...)          |
+----------------------+-------------------------------------------+
                       | JSON Lines
                       v
+------------------------------------------------------------------+
|                     CIMP Protocol Parser                          |
|  (cimp/frame.py)                                                  |
|  1. 逐行解析 JSON（兼容旧标签语法作为 fallback）                   |
|  2. 验证帧格式                                                     |
|  3. 按类型分发处理                                                  |
+------+---------+---------+---------+---------+------------------+
       |         |         |         |         |
       v         v         v         v         v
    text      card      media     voice   state_update
       |         |         |         |         |
       v         v         v         v         v
+------------------------------------------------------------------+
|                   Channel Renderer (cimp/renderer.py)             |
|  飞书: text+card -> interactive card                               |
|  企微: text -> text, card -> markdown(降级)                        |
|  QQ: text -> text/markdown, card -> markdown(降级)                 |
|  微信/小翼: text -> text, card -> 纯文本(降级)                      |
+------------------------------------------------------------------+
```

### 3.2 完整交互流程

```
[1] 用户发消息 "打开客厅灯"

[2] IM Hub -> AI:
    "[IM:Feishu|feishu:oc_xxx] {"type":"capabilities",...}\n打开客厅灯"

[3] AI 回复:
    {"type":"card","scene":"confirm","title":"确认操作",
     "body":"确定要打开客厅灯吗？",
     "buttons":[{"id":"yes","label":"确认","style":"primary"},
                {"id":"no","label":"取消","style":"default"}]}

[4] CIMP Parser 解析 -> card 帧
    -> 飞书 Renderer -> interactive card
    -> 用户看到带按钮的卡片

[5] 用户点击「确认」
    -> 飞书回调 FeishuCardCallbackView
    -> 提取上下文
    -> 构造 button_click 帧喂给 AI
    -> ask_home_assistant(button_click_frame, conversation_id=原ID)

[6] AI 收到 button_click 帧:
    "{"type":"button_click","button_id":"yes","button_label":"确认",
      "conversation_id":"feishu:oc_xxx"}"
    -> AI 理解这是在确认之前的问题
    -> 执行开灯动作
    -> 回复: {"type":"text","content":"客厅灯已打开  \u0001f4a1"}

[7] 飞书 Renderer -> interactive card -> 用户看到回复  \u2714\ufe0f
```

---

## 4. 代码改动清单

### 4.1 新增文件

#### (1) cimp/__init__.py - 空包

#### (2) cimp/frame.py - 帧定义 + 解析(~80 行)

核心接口:
- `parse_one(line: str) -> Frame | None` - 解析单行 JSON 为帧
- `parse_reply(reply: str) -> list[Frame]` - 解析 AI 回复为帧列表(含纯文本 fallback)
- `to_dict(frame: Frame) -> dict` - 序列化帧为字典

帧类型: TextFrame, CardFrame, MediaFrame, VoiceFrame,
         CapabilitiesFrame, ButtonClickFrame, StateUpdateFrame,
         RedirectFrame, ErrorFrame

#### (3) cimp/renderer.py - 渠道渲染器(~100 行)

渲染函数(按渠道):
- `render_feishu(frame) -> list[dict]` - 飞书: 构建 interactive card
- `render_wecom(frame) -> list[dict]` - 企微: card 降级为 markdown 文本
- `render_text_only(frame) -> list[dict]` - 纯文本渠道: card 降级为纯文本

每个渲染函数返回 `[{"msg_type": "...", "content": "..."}, ...]`

#### (4) cimp/upstream.py - 上游提示词(~50 行)

`build_cimp_prompt(channel, supports) -> str`
根据渠道能力生成 CIMP 协议提示词，注入 AI 的 extra_system_prompt。

### 4.2 修改文件

#### (1) providers/feishu.py (+~80 行)

改动 A - _handle_message:
- 调用 execute_command 前加入 extra_system_prompt(CIMP 能力声明)
- 回复解析从 _build_response_card 改为 parse_reply + render_feishu
- 按钮 value 中嵌入 _chat_id, _receive_type, _conversation_id

改动 B - FeishuCardCallbackView.post():
- 从按钮 value 提取上下文
- 构造 button_click 帧
- 调用 ask_home_assistant(button_click_frame, conversation_id)
- 解析 AI 回复并发回飞书

#### (2) command.py (+~8 行)
- execute_command 中: 如果 command.target 以 "{" 开头，不添加渠道前缀，直接传给 AI

#### (3) __init__.py (+~2 行)
- FeishuCardCallbackView 构造函数传入 agent_id

#### (4) upstream_prompt.py (+~5 行)
- build_upstream_extra_prompt 改为调用 cimp/upstream.py

---

## 5. 能力矩阵

| 渠道 | text | card | image | voice | buttons | approval | markdown | 卡片更新 |
|------|:----:|:----:|:-----:|:-----:|:-------:|:--------:|:--------:|:--------:|
| 飞书 | Y | Y | Y | - | Y | - | Y(卡片内) | Y |
| 企业微信 | Y | - | Y | - | - | - | Y | - |
| QQ | Y | - | Y | - | Y(需适配) | Y(需适配) | Y | - |
| 钉钉 | Y | Y | Y | - | Y | - | Y | - |
| 微信 | Y | - | Y | - | - | - | - | - |
| 小翼 | Y | - | - | - | - | - | - | - |

---

## 6. 向后兼容策略

| 场景 | 旧行为 | 新行为 |
|------|--------|--------|
| AI 回复纯文本 | rich_media 解析 [IMAGE:xxx] | parse_reply -> 无 JSON -> fallback 纯文本 |
| AI 回复 JSON Lines | 不识别，全当文本 | 精确解析每帧 |
| 卡片按钮回调 | 弹 toast 结束 | 回灌 button_click -> AI 处理 -> 发新回复 |
| send_message 服务 | 正常工作 | 正常工作，不依赖 CIMP |
| 其他 IM 渠道 | 正常工作 | 逐步接入 CIMP renderer |

---

## 7. 文件改动汇总

```
新增:
  custom_components/cn_im_hub/cimp/__init__.py      (空)
  custom_components/cn_im_hub/cimp/frame.py          (~80 行)
  custom_components/cn_im_hub/cimp/renderer.py       (~100 行)
  custom_components/cn_im_hub/cimp/upstream.py       (~50 行)

修改:
  custom_components/cn_im_hub/providers/feishu.py    (+~80 行)
  custom_components/cn_im_hub/command.py             (+~8 行)
  custom_components/cn_im_hub/__init__.py            (+~2 行)
  custom_components/cn_im_hub/upstream_prompt.py     (+~5 行)

总计: 新增 ~230 行，修改 ~95 行
```

---

## 8. 风险与缓解

| 风险 | 影响 | 缓解 |
|------|------|------|
| AI 不按 JSON Lines 输出 | 解析失败 | parse_reply 有纯文本 fallback |
| 按钮重复点击 | 重复执行操作 | value 嵌入 message_id 做幂等去重 |
| CallbackView 不知道 agent_id | 无法路由到正确的 AI | 构造函数传入 agent_id |
| 长文本超飞书 1800 限制 | 发送失败 | renderer 自动截断 |
| 旧系统升级后回调缺上下文 | 按钮点击无响应 | 兼容旧按钮 value，降级为 toast |
