"""Channel-specific CIMP frame renderers and Feishu v2 card builder.

This module provides:
1. CIMP frame renderers (for button callbacks and structured AI output)
2. Feishu v2 card builder with markdown support
3. Segment-to-card renderer (for normal AI replies)
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .frame import (
    Frame, TextFrame, CardFrame, MediaFrame, VoiceFrame,
    ButtonClickFrame, StateUpdateFrame, RedirectFrame,
)

if TYPE_CHECKING:
    from ..rich_media import Segment, TextSegment, ImageSegment

_LOGGER = logging.getLogger(__name__)
_REPLY_MAX_LENGTH = 1800

# Regex to extract (AgentName) 回复: prefix from multi-agent replies
_REPLY_PREFIX_RE = re.compile(r"\(([^)]+)\)\s*(?:回复|Reply)\s*[:：]\s*")


def _extract_title(text: str) -> tuple[str, str]:
    """Extract agent name from '(AgentName) 回复:' prefix."""
    match = _REPLY_PREFIX_RE.match(text)
    if match:
        return match.group(1).strip(), text[match.end():].lstrip()
    return "Claw AI 助手", text


# ---- Scene classification ----

_CONTROL_KEYWORDS = frozenset({
    "打开", "关闭", "控制", "设置", "调整", "开启", "停止",
})
_ALERT_KEYWORDS = frozenset({
    "报警", "警告", "异常", "故障", "错误", "危险", "注意", "⚠",
})
_CONFIRM_RE = re.compile(
    r"(是否\s*(要|需要|应该)|要不要|请[确认选择决定]|"
    r"[。！，]?\s*[吗么]$|[？?]\s*$|"
    r"确认|取消|允许|拒绝)"
)


def _classify_scene(text: str) -> str:
    """Classify the scene based on text content."""
    if any(kw in text for kw in _ALERT_KEYWORDS):
        return "alert"
    if _CONFIRM_RE.search(text):
        return "confirm"
    if any(kw in text for kw in _CONTROL_KEYWORDS):
        return "control"
    return "default"


# ---- Feishu v2 card builder ----

_FEISHU_STYLES = {
    "default": "blue",
    "control": "green",
    "alert": "red",
    "confirm": "orange",
    "approval": "purple",
}


def _build_feishu_v2_card(
    title: str = "Claw AI 助手",
    body_elements: list[dict] | None = None,
    scene: str = "default",
    buttons: list[dict] | None = None,
    footer: str | None = None,
) -> dict:
    """Build a Feishu card v2 format (schema: "2.0").

    The v2 format uses body.elements instead of top-level elements,
    and supports markdown components for rich text rendering.

    Args:
        title: Card header title.
        body_elements: List of element dicts (markdown, img, hr, etc.).
        scene: Scene type for color mapping.
        buttons: Optional list of button configs.
        footer: Optional footer text.

    Returns:
        Feishu card v2 JSON dict.
    """
    elements = list(body_elements) if body_elements else []

    # Add buttons if present
    if buttons:
        actions = []
        for btn in buttons:
            action_btn = {
                "tag": "button",
                "text": {"content": btn["label"], "tag": "lark_md"},
                "type": "primary" if btn.get("style") == "primary" else "default",
                "value": {"action": btn["id"]},
            }
            # Confirm popup
            if btn.get("confirm"):
                action_btn["confirm"] = {
                    "title": {"tag": "plain_text", "content": "确认操作"},
                    "body": {"tag": "plain_text", "content": f"确定要执行「{btn['label']}」吗？"},
                }
            # Toast message
            if btn.get("toast"):
                action_btn["value"]["toast"] = btn["toast"]
            actions.append(action_btn)
        elements.append({"tag": "action", "actions": actions})

    # Add footer note if present
    if footer:
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": footer}]
        })

    card = {
        "schema": "2.0",
        "config": {
            "wide_screen_mode": True,
        },
        "header": {
            "title": {"content": title, "tag": "plain_text"},
            "template": _FEISHU_STYLES.get(scene, "blue"),
        },
        # v2 uses body.elements instead of top-level elements
        "body": {
            "elements": elements,
        },
    }
    return card


# ---- Markdown to Feishu elements converter ----

def _text_to_card_elements(text: str) -> list[dict]:
    """Convert Markdown text to Feishu card v2 body.elements.

    Feishu markdown component supports:
    - **bold**, *italic*, ~~strikethrough~~
    - [text link](url)
    - ![image](img_key)
    - --- horizontal rule
    - Ordered/unordered lists (Feishu 7.6+)
    - Code blocks (Feishu 7.6+)
    - Tables

    Args:
        text: Markdown text from AI reply.

    Returns:
        List of Feishu card element dicts.
    """
    if not text or not text.strip():
        return []

    elements = []
    lines = text.strip().split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # H3 heading (### xxx) -> bold text
        if line.startswith("### "):
            elements.append({
                "tag": "markdown",
                "content": f"**{line[4:]}**"
            })

        # H2 heading (## xxx) -> hr + bold text
        elif line.startswith("## "):
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "markdown",
                "content": f"**{line[3:]}**"
            })

        # H1 heading (# xxx) -> hr + bold text
        elif line.startswith("# ") and not line.startswith("## "):
            elements.append({"tag": "hr"})
            elements.append({
                "tag": "markdown",
                "content": f"**{line[2:]}**"
            })

        # Unordered list (- / * / •)
        elif line.strip().startswith(("- ", "* ", "• ")):
            items = []
            while i < len(lines) and lines[i].strip().startswith(("- ", "* ", "• ")):
                items.append(lines[i].strip()[2:])
                i += 1
            md_list = "\n".join(f"- {item}" for item in items)
            elements.append({"tag": "markdown", "content": md_list})
            continue  # i already incremented

        # Ordered list (1. 2. 3.)
        elif re.match(r"^\d+\.\s", line.strip()):
            items = []
            while i < len(lines) and re.match(r"^\d+\.\s", lines[i].strip()):
                items.append(re.sub(r"^\d+\.\s", "", lines[i].strip()))
                i += 1
            md_list = "\n".join(f"{idx+1}. {item}" for idx, item in enumerate(items))
            elements.append({"tag": "markdown", "content": md_list})
            continue

        # Code block (```)
        elif line.strip().startswith("```"):
            lang = line.strip()[3:].strip()  # May have language identifier
            code_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            code_text = "\n".join(code_lines)
            if lang:
                elements.append({"tag": "markdown", "content": f"```{lang}\n{code_text}\n```"})
            else:
                elements.append({"tag": "markdown", "content": f"```\n{code_text}\n```"})

        # Table (| col1 | col2 |)
        elif "|" in line and i + 1 < len(lines) and "---" in lines[i + 1]:
            table_lines = []
            while i < len(lines) and "|" in lines[i]:
                table_lines.append(lines[i])
                i += 1
            elements.append({"tag": "markdown", "content": "\n".join(table_lines)})
            continue

        # Horizontal rule (--- or ***)
        elif re.match(r"^\s*[-*]{3,}\s*$", line):
            elements.append({"tag": "hr"})

        # Empty line -> skip
        elif not line.strip():
            pass

        # Normal text paragraph
        else:
            # Merge consecutive normal text lines into one markdown component
            para_lines = [line]
            while (i + 1 < len(lines)
                   and lines[i + 1].strip()
                   and not lines[i + 1].startswith("#")
                   and not lines[i + 1].strip().startswith(("- ", "* ", "• "))
                   and not re.match(r"^\d+\.\s", lines[i + 1].strip())
                   and not lines[i + 1].strip().startswith("```")
                   and not re.match(r"^\s*[-*]{3,}\s*$", lines[i + 1])):
                i += 1
                para_lines.append(lines[i])
            elements.append({
                "tag": "markdown",
                "content": "\n".join(para_lines)
            })

        i += 1

    return elements if elements else [{
        "tag": "markdown",
        "content": text[:_REPLY_MAX_LENGTH]
    }]


# ---- Image resolver ----

async def _resolve_image_for_feishu(
    hass,
    api_client: Any,
    source: str,
) -> str | None:
    """Resolve image source and upload to Feishu.

    Supports:
    - camera.xxx entity -> HA camera_proxy -> upload
    - http(s)://xxx URL -> download -> upload

    Args:
        hass: Home Assistant instance.
        api_client: FeishuApiClient instance with async_upload_image method.
        source: Image source (camera entity ID or URL).

    Returns:
        Feishu img_key if successful, None otherwise.
    """
    from ..rich_media import is_camera_entity, is_url

    image_bytes = None

    if is_camera_entity(source):
        # HA camera entity
        try:
            from homeassistant.components import camera
            image_bytes = await camera.async_get_image(hass, source)
        except Exception as err:
            _LOGGER.warning("Failed to get camera image %s: %s", source, err)
    elif is_url(source):
        # URL image
        try:
            from homeassistant.helpers.aiohttp_client import async_get_clientsession
            session = async_get_clientsession(hass)
            async with session.get(source, timeout=10) as resp:
                if resp.status == 200:
                    image_bytes = await resp.read()
        except Exception as err:
            _LOGGER.warning("Failed to download image %s: %s", source, err)

    if image_bytes:
        try:
            return await api_client.async_upload_image(image_bytes)
        except Exception as err:
            _LOGGER.warning("Failed to upload image to Feishu: %s", err)

    return None


# ---- Segment to card renderer ----

@dataclass(slots=True)
class CardRenderResult:
    """Result of rendering segments to card."""
    type: str  # "card" | "text" | "image"
    data: Any  # card dict, text string, or image source


async def render_segments_to_feishu_card(
    segments: list,
    *,
    title: str = "Claw AI 助手",
    hass=None,
    api_client: Any = None,
    receive_id: str = "",
    receive_type: str = "chat_id",
) -> list[CardRenderResult]:
    """Render a list of Segments to Feishu card(s).

    This is the main entry point for normal AI replies.
    It converts TextSegments to markdown elements and ImageSegments to img elements.

    Args:
        segments: List of Segment objects from rich_media.parse_reply_segments().
        title: Card header title (usually agent name).
        hass: Home Assistant instance (for image resolution).
        api_client: FeishuApiClient instance (for image upload).
        receive_id: Feishu receive ID (for context).
        receive_type: Feishu receive ID type.

    Returns:
        List of CardRenderResult objects.
    """
    from ..rich_media import (
        TextSegment, ImageSegment, VoiceSegment,
        FileSegment, VideoSegment, GifSegment,
        is_url,
    )

    results = []
    card_elements = []
    current_text_parts = []

    def flush_text():
        """Flush accumulated text to card elements."""
        nonlocal current_text_parts
        if current_text_parts:
            combined = "\n\n".join(current_text_parts)
            card_elements.extend(_text_to_card_elements(combined))
            current_text_parts = []

    for seg in segments:
        if isinstance(seg, TextSegment):
            current_text_parts.append(seg.text)

        elif isinstance(seg, ImageSegment):
            flush_text()
            # Try to resolve and upload image
            if api_client and hass:
                img_key = await _resolve_image_for_feishu(hass, api_client, seg.source)
                if img_key:
                    card_elements.append({
                        "tag": "img",
                        "img_key": img_key,
                        "alt": {"tag": "plain_text", "content": "图片"},
                    })
                else:
                    # Fallback to text link
                    if is_url(seg.source):
                        card_elements.append({
                            "tag": "markdown",
                            "content": f"[图片]({seg.source})"
                        })
                    else:
                        card_elements.append({
                            "tag": "markdown",
                            "content": f"图片: {seg.source}"
                        })

        elif isinstance(seg, VoiceSegment):
            flush_text()
            card_elements.append({
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": f"语音: {seg.text}"}]
            })

        elif isinstance(seg, (VideoSegment, FileSegment, GifSegment)):
            flush_text()
            kind = type(seg).__name__.replace("Segment", "").upper()
            src = seg.source if hasattr(seg, 'source') else str(seg)
            card_elements.append({
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": f"[{kind}: {src}]"}]
            })

    flush_text()

    if not card_elements:
        return [CardRenderResult("text", title)]

    # Classify scene and add confirm buttons if needed
    all_text = " ".join(
        seg.text for seg in segments if isinstance(seg, TextSegment)
    )
    scene = _classify_scene(all_text)
    buttons = None
    if scene == "confirm":
        buttons = [
            {"id": "confirm_yes", "label": "确认", "style": "primary", "toast": "已确认"},
            {"id": "confirm_no", "label": "取消", "style": "default", "toast": "已取消"},
        ]

    card = _build_feishu_v2_card(
        title=title,
        body_elements=card_elements,
        scene=scene,
        buttons=buttons,
    )
    return [CardRenderResult("card", card)]


# ---- Render results (for CIMP frames) ----

@dataclass(slots=True)
class RenderResult:
    """Rendered output ready for a provider to send."""
    msg_type: str  # "interactive" | "text" | "image" | "voice" | "file"
    content: str   # JSON string or plain text or source ref
    extra: dict[str, Any] | None = None  # extra info like file_name


# ---- CIMP frame renderers (for button callbacks) ----

def _feishu_card_v1(
    title: str,
    body: str,
    scene: str = "default",
    buttons: list[dict] | None = None,
) -> dict:
    """Build a Feishu interactive card v1 from CIMP fields.

    This is kept for backward compatibility with CIMP frame rendering.
    For normal AI replies, use _build_feishu_v2_card instead.
    """
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


def render_feishu(frame: Frame) -> list[RenderResult]:
    """Render a CIMP frame for Feishu sending.

    This is used for CIMP frame rendering (e.g., button callbacks).
    For normal AI replies, use render_segments_to_feishu_card instead.
    """
    match frame:
        case TextFrame():
            title, body = _extract_title(frame.content)
            # Use v2 card with markdown support
            elements = _text_to_card_elements(body)
            card = _build_feishu_v2_card(title=title, body_elements=elements)
            return [RenderResult("interactive", json.dumps(card))]
        case CardFrame():
            # CardFrame from CIMP uses v1 for backward compatibility
            card = _feishu_card_v1(
                frame.title or "操作确认",
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
