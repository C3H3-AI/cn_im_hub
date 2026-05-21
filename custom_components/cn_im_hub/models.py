"""Shared data models for CN IM Hub."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


ReplyFunc = Callable[[str], Awaitable[None]]


@dataclass(slots=True)
class Command:
    """Normalized command payload."""

    kind: str
    target: str
    payload: dict[str, Any]


@dataclass(slots=True)
class InboundContext:
    """Normalized inbound message from any IM provider."""

    provider: str
    text: str
    conversation_id: str
    reply: ReplyFunc


@dataclass(slots=True)
class ProviderRuntime:
    """Runtime wrapper for one provider instance."""

    key: str
    title: str
    subentry_id: str
    client: Any
    stop: Callable[[], Awaitable[None]]
    send_text: Callable[[str, str, str], Awaitable[None]]
    status: Callable[[], str]
    known_targets: Callable[[], list[dict[str, str]]]
    selected_target: Callable[[], str]
    select_target: Callable[[str], Awaitable[None]]
    send_image: Callable[[str, bytes, str], Awaitable[None]] | None = None
    send_media: Callable[[str, bytes, str, str, str | None], Awaitable[None]] | None = None
    send_tts: Callable[[str, str, str], Awaitable[None]] | None = None
    send_approval: Callable[[str, str, str, str], Awaitable[None]] | None = None
    send_card: Callable[[str, dict[str, Any], str], Awaitable[None]] | None = None


@dataclass(slots=True)
class HubRuntime:
    """Runtime data for one config entry."""

    providers: dict[str, ProviderRuntime]