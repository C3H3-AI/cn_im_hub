"""Channel-specific CIMP frame renderers."""
from __future__ import annotations

import json
import re
from typing import Any
from dataclasses import dataclass

from .frame import (
    Frame, TextFrame, CardFrame, MediaFrame, VoiceFrame,
    ButtonClickFrame, StateUpdateFrame, RedirectFrame,
)

_REPLY_MAX_LENGTH = 1800

# Regex to extract (AgentName) 回复: prefix from multi-agent replies
_REPLY_PREFIX_RE = re.compile(r"\(([^)]+)\)\s*(?:回复|Reply)\s*[:：]\s*")


def _extract_title(text: str) -> tuple[str, str]:
    """Extract agent name from '(AgentName) 回复:' prefix."""
    match = _REPLY_PREFIX_RE.match(text)
    if match:
        return match.group(1).strip(), text[match.end():].lstrip()
    return "Claw AI 管家", text


# ---- Feishu card builder ----

_FEISHU_STYLES = {
    "default": "blue",
    "control": "green",
    "alert": "red",
    "confirm": "orange",
    "approval": "purple",
}


def _feishu_card(
    title: str,
    body: str,
    scene: str = "default",
    buttons: list[dict] | None = None,
) -> dict:
    """Build a Feishu interactive card from CIMP fields."""
    elements: list[dict] = [
        {
            "tag": "div",
            "text": {
                "content": body[:_REPLY_MAX_LENGTH],
                "tag": "plain_text",
            },
        },
    ]
    if buttons:
        actions = []
        for btn in buttons:
            btn_type = "primary" if btn.get("style") == "primary" else "default"
            actions.append({
                "tag": "button",
                "text": {"content": btn["label"], "tag": "lark_md"},
                "type": btn_type,
                "value": {"action": btn["id"]},
            })
        elements.append({"tag": "action", "actions": actions})
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": title, "tag": "plain_text"},
            "template": _FEISHU_STYLES.get(scene, "blue"),
        },
        "elements": elements,
    }


# ---- Render results ----

@dataclass(slots=True)
class RenderResult:
    """Rendered output ready for a provider to send."""
    msg_type: str  # "interactive" | "text" | "image" | "voice" | "file"
    content: str   # JSON string or plain text or source ref
    extra: dict[str, Any] | None = None  # extra info like file_name


# ---- Renderers ----

def render_feishu(frame: Frame) -> list[RenderResult]:
    """Render a CIMP frame for Feishu sending."""
    match frame:
        case TextFrame():
            title, body = _extract_title(frame.content)
            card = _feishu_card(title, body, "default")
            return [RenderResult("interactive", json.dumps(card))]
        case CardFrame():
            card = _feishu_card(
                frame.title or "\u64cd\u4f5c\u786e\u8ba4",
                frame.body,
                frame.scene,
                frame.buttons,
            )
            return [RenderResult("interactive", json.dumps(card))]
        case MediaFrame() if frame.kind in ("image", "gif"):
            return [RenderResult("image", frame.source)]
        case MediaFrame():
            return [RenderResult("text", f"[{frame.kind}: {frame.source}]")]
        case VoiceFrame():
            # Feishu doesn't support direct TTS; downgrade to text
            return [RenderResult("text", frame.text)]
        case StateUpdateFrame():
            # Feishu supports card update via message_id
            # Currently return as text, can be enhanced
            status = frame.fields.get("status", "")
            return [RenderResult("text", status)]
        case _:
            return [RenderResult("text", str(frame))]


def render_wecom(frame: Frame) -> list[RenderResult]:
    """Render a CIMP frame for WeCom.
    WeCom doesn't support cards, so card/markdown are downgraded.
    """
    match frame:
        case CardFrame():
            text = f"## {frame.title}\n\n{frame.body}"
            if frame.buttons:
                labels = " / ".join(b["label"] for b in frame.buttons)
                text += f"\n\n{labels}"
            return [RenderResult("text", text)]
        case TextFrame():
            return [RenderResult("text", frame.content)]
        case MediaFrame() if frame.kind == "image":
            return [RenderResult("image", frame.source)]
        case _:
            return [RenderResult("text", str(frame))]


def render_text_only(frame: Frame) -> list[RenderResult]:
    """Render for channels that only support plain text.
    (WeChat, XiaoYi, DingTalk, etc.)
    """
    match frame:
        case CardFrame():
            text = f"{frame.title}: {frame.body}"
            return [RenderResult("text", text)]
        case TextFrame():
            return [RenderResult("text", frame.content)]
        case MediaFrame():
            return [RenderResult("text", f"[{frame.kind}: {frame.source}]")]
        case VoiceFrame():
            return [RenderResult("text", frame.text)]
        case _:
            return [RenderResult("text", str(frame))]


# ---- Registry ----

RENDERERS: dict[str, Any] = {
    "feishu": render_feishu,
    "wecom": render_wecom,
    "qq": render_text_only,
    "dingtalk": render_text_only,
    "wechat": render_text_only,
    "xiaoyi": render_text_only,
}
