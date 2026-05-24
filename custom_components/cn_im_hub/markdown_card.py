"""Markdown to Feishu Card converter.

Converts native claw Markdown output to Feishu card v2 format.
All messages are unified into card format for consistency.
"""

from __future__ import annotations

import re
from typing import Any


def markdown_to_feishu_card(
    text: str,
    *,
    title: str = "Claw AI 助手",
    template: str = "blue",
) -> dict[str, Any]:
    """Convert Markdown text to Feishu card v2 format.

    Args:
        text: Markdown text from claw.
        title: Card header title.
        template: Color template (blue/green/red/orange).

    Returns:
        Feishu card v2 JSON dict.
    """
    elements = []

    # Parse and convert content
    content = text.strip()

    # Handle rich media tags [IMAGE:xxx], [VIDEO:xxx], etc.
    content = _convert_rich_media_tags(content, elements)

    # Convert remaining markdown to card elements
    if content.strip():
        elements.append({
            "tag": "markdown",
            "content": content,
        })

    # Build card
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": title, "tag": "plain_text"},
            "template": template,
        },
        "body": {"elements": elements},
    }


def _convert_rich_media_tags(content: str, elements: list[dict]) -> str:
    """Convert rich media tags to card elements.

    Args:
        content: Original content.
        elements: List to append card elements to.

    Returns:
        Remaining content after extracting media tags.
    """
    # Pattern for [IMAGE:entity_id] or [IMAGE:/path]
    image_pattern = r'\[IMAGE:\s*([^\]]+)\]'

    # Split content by image tags
    parts = re.split(image_pattern, content)

    result_parts = []
    for i, part in enumerate(parts):
        if i % 2 == 0:
            # Text part
            if part.strip():
                result_parts.append(part)
        else:
            # Image tag content
            image_ref = part.strip()
            # Add image placeholder (will be resolved later)
            elements.append({
                "tag": "markdown",
                "content": f"📷 *图片: {image_ref}*",
            })

    return ''.join(result_parts)


def should_use_card(text: str) -> bool:
    """Check if text should be converted to card.

    Currently always returns True for unified experience.

    Args:
        text: Message content.

    Returns:
        Always True.
    """
    return True


def build_simple_text_card(text: str, title: str = "Claw AI 助手") -> dict[str, Any]:
    """Build a simple text-only card.

    Args:
        text: Plain text content.
        title: Card title.

    Returns:
        Feishu card dict.
    """
    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": title, "tag": "plain_text"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {
                    "tag": "markdown",
                    "content": text,
                }
            ]
        },
    }
