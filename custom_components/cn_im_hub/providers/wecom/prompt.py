from __future__ import annotations

from ...media.tts import is_edge_tts_available

_WECOM_PROMPT = (
    "## IM Channel Delivery\n"
    "Your reply is delivered through WeCom (企业微信) as chat messages.\n"
    "\n"
    "### Style Guidelines\n"
    "- Messages are rendered with Markdown support.\n"
    "- Supported: **bold**, `inline code`, ```code blocks```, links, numbered/bullet lists.\n"
    "- Links: [text](url) format is supported.\n"
    "- Emoji are fine; use naturally where appropriate.\n"
    "- Keep paragraphs concise for mobile readability.\n"
    "\n"
    "### Media Tag Rules\n"
    "- Current channel: **WeCom** (企业微信). All media is delivered natively by the WeCom API.\n"
    "- Each media tag must appear on its own line.\n"
    "- Source inside tags MUST be a plain string (entity_id, URL, or path). NEVER wrap in HTML (<a>, <img>) or markdown links.\n"
    "- **CRITICAL**: For local files, ALWAYS use `/local/claw_assistant/...` path format, NEVER use absolute system paths like `/Users/.../config/www/...` or `/config/www/...`.\n"
    "\n"
    "### Available Media Tags\n"
    "- [IMAGE:camera.entity_id] or [IMAGE:https://url] or [IMAGE:/local/claw_assistant/file.png] — deliver an image or camera snapshot.\n"
    "- For home cameras/devices, ALWAYS use entity_id (e.g. [IMAGE:camera.front_door]), never use internal/external IP URLs.\n"
    "- Use text for explanation; use [IMAGE:...] only when you want the image delivered.\n"
    "- [FILE:/local/claw_assistant/file.ext] or [FILE:https://url] — send a file.\n"
    "- [VIDEO:camera.entity_id], [VIDEO:/local/claw_assistant/video.mp4], or [VIDEO:https://url] — send a video.\n"
    "- For home cameras, use entity_id (e.g. [VIDEO:camera.front_door]) to record a clip via HA, not IP URLs.\n"
)

_WECOM_VOICE_HINT = (
    "- [VOICE:要说的话] — synthesize and send a spoken audio file.\n"
    "- Content must be user-facing only. No agent names, prefixes, or metadata.\n"
)


def build_wecom_prompt() -> str:
    prompt = _WECOM_PROMPT
    if is_edge_tts_available():
        prompt += _WECOM_VOICE_HINT
    return prompt.strip()
