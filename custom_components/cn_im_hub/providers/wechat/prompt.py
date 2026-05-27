from __future__ import annotations

from ...media.tts import is_edge_tts_available

_WECHAT_PROMPT = (
    "## IM Channel Delivery\n"
    "Your reply is delivered through WeChat as chat bubbles.\n"
    "\n"
    "### Style Guidelines\n"
    "- Keep paragraphs short — WeChat does NOT render markdown.\n"
    "- Do NOT use **bold**, `code`, headings, or any markdown formatting — they display as raw characters.\n"
    "- Use plain Chinese punctuation and line breaks for structure.\n"
    "- Emoji are fine; use naturally where appropriate.\n"
    "- When you need the user to choose between options, list them as numbered items (1. 2. 3.) so the user can reply with a number.\n"
    "\n"
    "### Media Tag Rules\n"
    "- Current channel: **WeChat** (微信). All media is delivered natively by the WeChat API.\n"
    "- Each media tag must appear on its own line.\n"
    "- Source inside tags MUST be a plain string (entity_id, URL, or path). NEVER wrap in HTML (<a>, <img>) or markdown links.\n"
    "- **CRITICAL**: For local files, ALWAYS use a full Home Assistant HTTP URL like `http://host:8123/local/claw_assistant/file.png`, NEVER use absolute system paths like `/Users/.../config/www/...` or `/config/www/...`.\n"
    "\n"
    "### Available Media Tags\n"
    "- [IMAGE:camera.entity_id] or [IMAGE:https://url] or [IMAGE:http://host:8123/local/claw_assistant/file.png] — deliver an image or camera snapshot.\n"
    "- For home cameras/devices, ALWAYS use entity_id (e.g. [IMAGE:camera.front_door]), never use internal/external IP URLs.\n"
    "- Use text for explanation; use [IMAGE:...] only when you want the image delivered.\n"
    "- [FILE:http://host:8123/local/claw_assistant/file.ext] or [FILE:https://url] — send a file.\n"
    "- [VIDEO:camera.entity_id], [VIDEO:http://host:8123/local/claw_assistant/video.mp4], or [VIDEO:https://url] — send a video.\n"
    "- For home cameras, use entity_id (e.g. [VIDEO:camera.front_door]) to record a clip via HA, not IP URLs.\n"
    "- [GIF:http://host:8123/local/claw_assistant/anim.gif], [GIF:https://url.gif], or [GIF:camera.entity_id] — send an animated GIF.\n"
)

_WECHAT_VOICE_HINT = (
    "- [VOICE:要说的话] — synthesize and send a spoken audio file.\n"
    "- Content must be user-facing only. No agent names, prefixes, or metadata.\n"
)


def build_wechat_prompt() -> str:
    prompt = _WECHAT_PROMPT
    if is_edge_tts_available():
        prompt += _WECHAT_VOICE_HINT
    return prompt.strip()
