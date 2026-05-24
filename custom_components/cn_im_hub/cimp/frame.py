"""CIMP frame definitions and parser."""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(slots=True)
class TextFrame:
    """Plain text reply."""
    type: str = "text"
    content: str = ""
    conversation_id: str = ""


@dataclass(slots=True)
class CardFrame:
    """Interactive card."""
    type: str = "card"
    scene: str = "default"  # default | control | alert | confirm | approval
    title: str = ""
    body: str = ""
    buttons: list[dict] = field(default_factory=list)
    conversation_id: str = ""


@dataclass(slots=True)
class MediaFrame:
    """Media message."""
    type: str = "media"
    kind: str = "image"  # image | video | gif | file | audio
    source: str = ""
    file_name: str = ""
    caption: str = ""


@dataclass(slots=True)
class VoiceFrame:
    """Voice/TTS message."""
    type: str = "voice"
    text: str = ""
    conversation_id: str = ""


@dataclass(slots=True)
class CapabilitiesFrame:
    """Channel capability declaration (IM Hub -> AI)."""
    type: str = "capabilities"
    channel: str = ""
    supports: list[str] = field(default_factory=list)
    max_text_length: int = 0
    conversation_id: str = ""


@dataclass(slots=True)
class ButtonClickFrame:
    """Card button click callback (IM Hub -> AI)."""
    type: str = "button_click"
    button_id: str = ""
    card_id: str = ""
    button_label: str = ""
    conversation_id: str = ""
    chat_id: str = ""
    user_id: str = ""


@dataclass(slots=True)
class StateUpdateFrame:
    """Update a previously sent card."""
    type: str = "state_update"
    card_id: str = ""
    fields: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class RedirectFrame:
    """Cross-conversation redirect."""
    type: str = "redirect"
    target: str = ""
    content: str = ""
    media: dict | None = None


@dataclass(slots=True)
class ErrorFrame:
    """Error response."""
    type: str = "error"
    code: str = ""
    message: str = ""


Frame = TextFrame | CardFrame | MediaFrame | VoiceFrame | \
        CapabilitiesFrame | ButtonClickFrame | StateUpdateFrame | \
        RedirectFrame | ErrorFrame


FRAME_CLASSES: dict[str, type] = {
    "text": TextFrame,
    "card": CardFrame,
    "media": MediaFrame,
    "voice": VoiceFrame,
    "capabilities": CapabilitiesFrame,
    "button_click": ButtonClickFrame,
    "state_update": StateUpdateFrame,
    "redirect": RedirectFrame,
    "error": ErrorFrame,
}


def parse_one(line: str) -> Frame | None:
    """Parse a single JSON line into a Frame object."""
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    cls = FRAME_CLASSES.get(data.get("type"))
    if cls is None:
        return None
    valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
    return cls(**{k: v for k, v in data.items() if k in valid_keys})


def parse_reply(reply: str) -> list[Frame]:
    """Parse AI reply text into a list of CIMP frames.
    Falls back to plain text frame if no JSON frames found.
    """
    frames: list[Frame] = []
    for line in reply.strip().split("\n"):
        frame = parse_one(line)
        if frame:
            frames.append(frame)
    if frames:
        return frames
    # Legacy fallback: treat entire reply as one text frame
    text = reply.strip()
    if text:
        return [TextFrame(content=text)]
    return []


def to_dict(frame: Frame) -> dict[str, Any]:
    """Serialize a Frame to a dict, dropping empty default values."""
    d = asdict(frame)
    return {k: v for k, v in d.items() if v != "" and v != [] and v != 0 and v is not None}


def serialize(frame: Frame) -> str:
    """Serialize a Frame to a JSON string."""
    return json.dumps(to_dict(frame), ensure_ascii=False)
