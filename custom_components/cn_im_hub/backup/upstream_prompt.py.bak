"""Dynamic upstream prompt helpers for IM providers."""

from __future__ import annotations


def build_upstream_extra_prompt(
    *,
    supports_image: bool = False,
    supports_voice: bool = False,
    supports_file: bool = False,
    supports_video: bool = False,
    supports_gif: bool = False,
) -> str | None:
    """Build provider-side capability guidance for the upstream conversation agent."""

    lines: list[str] = []

    if supports_image:
        lines.extend(
            [
                "## Upstream Capabilities",
                "- This upstream can deliver images back to the user.",
                "- To send an image or camera snapshot, output `[IMAGE:camera.entity_id]` or `[IMAGE:https://url]` on its own line.",
                "- Use normal text for explanation or analysis. Use `[IMAGE:...]` only when you want the image delivered.",
                "- In media tags, use the raw path or raw URL only. Do not wrap it in HTML or markdown links.",
            ]
        )

    if supports_voice:
        if not lines:
            lines.append("## Upstream Capabilities")
        lines.extend(
            [
                "- This upstream can synthesize and send spoken replies.",
                "- When voice is the best format, output a single `[VOICE:要说的话]` line.",
                "- Keep `[VOICE:...]` content user-facing only. Do not include agent names, prefixes, or metadata.",
            ]
        )

    if supports_file:
        if not lines:
            lines.append("## Upstream Capabilities")
        lines.extend(
            [
                "- This upstream can send files back to the user.",
                "- To send a file, output `[FILE:/absolute/path]` or `[FILE:https://url]` on its own line.",
                "- Use the raw path or raw URL only, not an HTML anchor or markdown link.",
            ]
        )

    if supports_video:
        if not lines:
            lines.append("## Upstream Capabilities")
        lines.extend(
            [
                "- This upstream can send video back to the user.",
                "- To send a video, output `[VIDEO:camera.entity_id]`, `[VIDEO:/absolute/path]`, or `[VIDEO:https://url]` on its own line.",
                "- Use the raw path or raw URL only, not an HTML anchor or markdown link.",
            ]
        )

    if supports_gif:
        if not lines:
            lines.append("## Upstream Capabilities")
        lines.extend(
            [
                "- This upstream can send animated GIF images.",
                "- To send a GIF, output `[GIF:/absolute/path.gif]`, `[GIF:https://url.gif]`, or `[GIF:camera.entity_id]` on its own line.",
                "- Use the raw path or raw URL only, not an HTML anchor or markdown link.",
            ]
        )

    return "\n".join(lines).strip() or None
