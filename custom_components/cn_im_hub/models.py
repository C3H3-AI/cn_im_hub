from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


ReplyFunc = Callable[[str], Awaitable[None]]
StopFunc = Callable[[], Awaitable[None]]
TextSender = Callable[[str, str, str], Awaitable[None]]
ImageSender = Callable[[str, bytes, str], Awaitable[None]]
VideoSender = Callable[[str, bytes, str, str], Awaitable[None]]  # target, bytes, filename, target_type
FileSender = Callable[[str, bytes, str, str], Awaitable[None]]  # target, bytes, filename, target_type
VoiceSender = Callable[[str, bytes, str, str], Awaitable[None]]  # target, bytes, filename, target_type
MediaSender = Callable[[str, bytes, str, str, str | None], Awaitable[None]]
TtsSender = Callable[[str, str, str], Awaitable[None]]
ApprovalSender = Callable[[str, str, str, str], Awaitable[None]]
CardSender = Callable[[str, dict[str, Any], str], Awaitable[None]]
StatusFunc = Callable[[], str]
KnownTargetsFunc = Callable[[], list[dict[str, str]]]
SelectedTargetFunc = Callable[[], str]
SelectTargetFunc = Callable[[str], Awaitable[None]]


CAPABILITY_REGISTRY: dict[str, str] = {
    "text": "send_text",
    "image": "send_image",
    "video": "send_video",
    "file": "send_file",
    "voice": "send_voice",
    "media": "send_media",
    "tts": "send_tts",
    "approval": "send_approval",
    "card": "send_card",
}


@dataclass(slots=True)
class Command:
    kind: str
    target: str
    payload: dict[str, Any]


@dataclass(slots=True)
class InboundContext:
    provider: str
    text: str
    conversation_id: str
    reply: ReplyFunc


@dataclass(slots=True)
class ProviderRuntime:
    key: str
    title: str
    subentry_id: str
    client: Any
    stop: StopFunc
    send_text: TextSender
    status: StatusFunc
    known_targets: KnownTargetsFunc
    selected_target: SelectedTargetFunc
    select_target: SelectTargetFunc
    send_image: ImageSender | None = None
    send_video: VideoSender | None = None
    send_file: FileSender | None = None
    send_voice: VoiceSender | None = None
    send_media: MediaSender | None = None
    send_tts: TtsSender | None = None
    send_approval: ApprovalSender | None = None
    send_card: CardSender | None = None

    @property
    def capabilities(self) -> list[str]:
        return [cap for cap, attr in CAPABILITY_REGISTRY.items() if getattr(self, attr, None) is not None]

    @property
    def capability_tier(self) -> str:
        n = len(self.capabilities)
        return "rich" if n >= 4 else "standard" if n >= 2 else "basic"


@dataclass(slots=True)
class HubRuntime:
    providers: dict[str, ProviderRuntime]


def command_factory(kind: str, target: str, payload: dict[str, Any] | None = None) -> Command:
    return Command(kind=kind, target=target, payload=payload or {})


def inbound_context_factory(provider: str, text: str, conversation_id: str, reply: ReplyFunc) -> InboundContext:
    return InboundContext(provider=provider, text=text, conversation_id=conversation_id, reply=reply)


def hub_runtime_factory(providers: dict[str, ProviderRuntime] | None = None) -> HubRuntime:
    return HubRuntime(providers=providers or {})