from __future__ import annotations

_CONTEXT_BASE = (
    "## IM Channel Delivery\n"
    "Your reply is delivered through an IM channel (QQ, WeChat, etc.) as chat bubbles.\n"
    "\n"
    "### Style Guidelines\n"
    "- Keep paragraphs short for readability.\n"
    "- Supported markdown: **bold**, `inline code`, ```code blocks```, tables, numbered/bullet lists, horizontal rules (---).\n"
    "- NOT supported: headings (#), [text](url) links. Use **bold** instead of headings. Paste raw URLs directly.\n"
    "- Emoji are fine; use naturally where appropriate.\n"
)

_CARD_HINT = "- When you need the user to choose between options, ALWAYS use a [CARD:...] tag instead of listing choices in text.\n"
_NO_CARD_HINT = "- When you need the user to choose between options, list them as numbered items (1. 2. 3.) so the user can reply with a number.\n"

_MEDIA_RULES = (
    "### Media Tag Rules\n"
    "- Each media tag must appear on its own line.\n"
    "- Use raw source only inside tags. Do NOT wrap in markdown links or HTML.\n"
)

_MEDIA_TAGS: dict[str, list[str]] = {
    "image": [
        "[IMAGE:camera.entity_id] or [IMAGE:https://url] — deliver an image or camera snapshot.",
        "For home cameras/devices, ALWAYS use entity_id (e.g. [IMAGE:camera.front_door]), never use internal/external IP URLs.",
        "Use text for explanation; use [IMAGE:...] only when you want the image delivered.",
    ],
    "voice": [
        "[VOICE:要说的话] — synthesize and send a spoken reply.",
        "Content must be user-facing only. No agent names, prefixes, or metadata.",
    ],
    "file": [
        "[FILE:/absolute/path] or [FILE:https://url] — send a file.",
    ],
    "video": [
        "[VIDEO:camera.entity_id], [VIDEO:/absolute/path], or [VIDEO:https://url] — send a video.",
        "For home cameras, use entity_id (e.g. [VIDEO:camera.front_door]) to record a clip via HA, not IP URLs.",
    ],
    "gif": [
        "[GIF:/absolute/path.gif], [GIF:https://url.gif], or [GIF:camera.entity_id] — send an animated GIF.",
    ],
    "card": [
        '[CARD:{"text":"提示","buttons":[["选项A","选项B"],["选项C"]]}] — interactive buttons.',
        "Simple: [CARD:提示文字|选项A,选项B|选项C] — pipe separates rows, comma separates buttons.",
        "User taps a button; their selection is fed back to you automatically as a new message.",
        "ALWAYS use [CARD:...] when the user needs to choose between options — never list choices in text.",
    ],
}


def build_upstream_extra_prompt(
    *,
    supports_image: bool = False,
    supports_voice: bool = False,
    supports_file: bool = False,
    supports_video: bool = False,
    supports_gif: bool = False,
    supports_card: bool = False,
    supports_markdown: bool = False,
) -> str | None:
    caps = {
        "image": supports_image,
        "voice": supports_voice,
        "file": supports_file,
        "video": supports_video,
        "gif": supports_gif,
        "card": supports_card,
    }
    active = [k for k, v in caps.items() if v]
    if not active:
        return None
    context = _CONTEXT_BASE + (_CARD_HINT if supports_card else _NO_CARD_HINT)
    lines = [context, _MEDIA_RULES, "### Available Media Tags"]
    for key in active:
        for line in _MEDIA_TAGS[key]:
            lines.append(f"- {line}")
    return "\n".join(lines).strip()
