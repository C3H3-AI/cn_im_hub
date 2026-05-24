"""Dynamic upstream prompt helpers for IM providers."""

from __future__ import annotations

from .cimp.upstream import build_cimp_prompt


def build_upstream_extra_prompt(
    *,
    channel: str = "",
    supports: list[str] | None = None,
    supports_image: bool = False,
    supports_voice: bool = False,
    supports_file: bool = False,
    supports_video: bool = False,
    supports_gif: bool = False,
) -> str | None:
    """Build provider-side capability guidance for the upstream conversation agent.
    Delegates to CIMP protocol when channel is known.
    """
    if channel and supports is not None:
        return build_cimp_prompt(channel, supports)
    # Legacy fallback for backward compatibility
    legacy_supports = []
    if supports_image:
        legacy_supports.append("image")
    if supports_voice:
        legacy_supports.append("voice")
    if supports_file:
        legacy_supports.append("file")
    if supports_video:
        legacy_supports.append("video")
    if supports_gif:
        legacy_supports.append("gif")
    if legacy_supports:
        return build_cimp_prompt(channel or "unknown", legacy_supports)
    return None
