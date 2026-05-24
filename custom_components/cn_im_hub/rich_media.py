"""Parse rich-media tags from AI reply text and split into segments.

This module is imported from the official cn_im_hub repository (ha-china/cn_im_hub).
It provides a unified way to parse [IMAGE:xxx], [VOICE:xxx], etc. tags from AI replies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


_TAG_RE = re.compile(r"\[(IMAGE|VOICE|FILE|VIDEO|GIF):(.+?)\]")
_AGENT_PREFIX_ONLY = re.compile(r"^\(.+?\)\s*(?:回复|Reply)\s*[:：]?\s*$")


@dataclass(slots=True)
class TextSegment:
    """Plain text segment."""
    text: str


@dataclass(slots=True)
class ImageSegment:
    """Image segment with source (camera entity or URL)."""
    source: str


@dataclass(slots=True)
class VoiceSegment:
    """Voice/TTS segment with text content."""
    text: str


@dataclass(slots=True)
class FileSegment:
    """File segment with source URL."""
    source: str


@dataclass(slots=True)
class VideoSegment:
    """Video segment with source URL."""
    source: str


@dataclass(slots=True)
class GifSegment:
    """GIF segment with source URL."""
    source: str


Segment = TextSegment | ImageSegment | VoiceSegment | FileSegment | VideoSegment | GifSegment


def parse_reply_segments(reply: str) -> list[Segment]:
    """Parse AI reply text into a list of segments.

    Splits the reply by [IMAGE:xxx], [VOICE:xxx], etc. tags.
    Returns a list of TextSegment and media Segment objects.

    Args:
        reply: The AI reply text, possibly containing rich-media tags.

    Returns:
        A list of Segment objects (TextSegment, ImageSegment, etc.).
    """
    segments: list[Segment] = []
    last_end = 0

    for match in _TAG_RE.finditer(reply):
        # Text before the tag
        before = reply[last_end : match.start()].strip()
        if before:
            segments.append(TextSegment(text=before))

        # The tag itself
        tag = match.group(1).strip().upper()
        payload = match.group(2).strip()

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

        last_end = match.end()

    # Trailing text after the last tag
    trailing = reply[last_end:].strip()
    if trailing:
        segments.append(TextSegment(text=trailing))

    # If no segments were created, return the entire reply as one text segment
    if not segments and reply.strip():
        segments.append(TextSegment(text=reply.strip()))

    # Handle agent prefix like "(AgentName) 回复:" that appears alone
    has_media = any(
        isinstance(s, ImageSegment | VoiceSegment | FileSegment | VideoSegment | GifSegment)
        for s in segments
    )
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


def is_camera_entity(source: str) -> bool:
    """Check if the source is a Home Assistant camera entity ID."""
    return source.startswith("camera.")


def is_url(source: str) -> bool:
    """Check if the source is a URL."""
    return source.startswith("http://") or source.startswith("https://")
