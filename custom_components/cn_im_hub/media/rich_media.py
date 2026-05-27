"""Parse rich-media tags from AI reply text and split into segments."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TAG_RE = re.compile(r"\[(IMAGE|VOICE|FILE|VIDEO|GIF|CARD):(.+?)\]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_AGENT_PREFIX_ONLY = re.compile(r"^\(.+?\)\s*(?:回复|Reply)\s*[:：]?\s*$")
_AGENT_PREFIX_RE = re.compile(r"^\((.+?)\)\s*(?:回复|Reply)\s*[:：]\s*", re.DOTALL)


@dataclass(slots=True)
class TextSegment:
    text: str


@dataclass(slots=True)
class ImageSegment:
    source: str


@dataclass(slots=True)
class VoiceSegment:
    text: str


@dataclass(slots=True)
class FileSegment:
    source: str


@dataclass(slots=True)
class VideoSegment:
    source: str


@dataclass(slots=True)
class GifSegment:
    source: str


@dataclass(slots=True)
class CardSegment:
    source: str


Segment = TextSegment | ImageSegment | VoiceSegment | FileSegment | VideoSegment | GifSegment | CardSegment


def parse_reply_segments(reply: str) -> list[Segment]:
    segments: list[Segment] = []
    last_end = 0
    for match in _TAG_RE.finditer(reply):
        before = reply[last_end : match.start()].strip()
        if before:
            segments.append(TextSegment(text=before))
        tag = match.group(1).strip().upper()
        payload = _HTML_TAG_RE.sub("", match.group(2)).strip()
        if tag == "IMAGE":
            segments.append(ImageSegment(source=payload))
        elif tag == "VOICE" and payload:
            segments.append(VoiceSegment(text=payload))
        elif tag == "FILE" and payload:
            segments.append(FileSegment(source=payload))
        elif tag == "VIDEO" and payload:
            segments.append(VideoSegment(source=payload))
        elif tag == "GIF" and payload:
            segments.append(GifSegment(source=payload))
        elif tag == "CARD" and payload:
            segments.append(CardSegment(source=payload))
        last_end = match.end()
    trailing = reply[last_end:].strip()
    if trailing:
        segments.append(TextSegment(text=trailing))
    if not segments and reply.strip():
        segments.append(TextSegment(text=reply.strip()))
    has_media = any(isinstance(s, ImageSegment | VoiceSegment | FileSegment | VideoSegment | GifSegment | CardSegment) for s in segments)
    if has_media:
        prefix_text: str | None = None
        has_voice = any(isinstance(s, VoiceSegment) for s in segments)
        for i, s in enumerate(segments):
            if isinstance(s, TextSegment) and _AGENT_PREFIX_ONLY.match(s.text):
                prefix_text = s.text.rstrip(":： \t")
                segments[i] = None  # type: ignore[assignment]
            elif prefix_text and isinstance(s, TextSegment):
                segments[i] = TextSegment(text=f"{prefix_text}: {s.text}")
                prefix_text = None
        if prefix_text and not has_voice:
            segments.append(TextSegment(text=prefix_text))
        segments = [s for s in segments if s is not None]
    return segments


def extract_reply_prefix(reply: str) -> tuple[str, str]:
    m = _AGENT_PREFIX_RE.match(reply)
    if m:
        return m.group(1).strip(), reply[m.end():].strip()
    return "", reply


def is_camera_entity(source: str) -> bool:
    return source.startswith("camera.")


def is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")