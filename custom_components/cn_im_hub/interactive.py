"""Interactive markup parser and renderer for IM channels.

This module parses interactive markup from AI replies and renders them
as platform-specific interactive elements (cards, buttons, etc.).

Markup syntax:
    [INTERACTIVE type="confirm|select|input" id="xxx" ...]
    [OPTION id="a" label="Option A"]
    [/INTERACTIVE]

See: D:\ai-hub\memory\shared\interactive_markup_spec.md
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# Regex patterns for parsing interactive markup
_INTERACTIVE_START_RE = re.compile(
    r'\[INTERACTIVE\s+'
    r'type="(?P<type>[^"]+)"\s+'
    r'id="(?P<id>[^"]+)"'
    r'(?P<attrs>[^\]]*)\]'
)
_INTERACTIVE_END_RE = re.compile(r'\[/INTERACTIVE\]')
_OPTION_RE = re.compile(
    r'\[OPTION\s+'
    r'id="(?P<id>[^"]+)"\s+'
    r'label="(?P<label>[^"]+)"'
    r'(?P<attrs>[^\]]*)\]'
)
_ATTR_RE = re.compile(r'(\w+)="([^"]*)"')


@dataclass(slots=True)
class InteractiveBlock:
    """Represents an interactive block in the markup."""
    block_type: str  # confirm, select, input, multi_step
    block_id: str
    message: str = ""
    options: list[dict] = field(default_factory=list)
    attrs: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class ParsedContent:
    """Result of parsing content with interactive markup."""
    text_segments: list[str]  # Plain text parts
    interactive_blocks: list[InteractiveBlock]  # Interactive blocks


def parse_interactive_markup(content: str) -> ParsedContent:
    """Parse content containing interactive markup.

    Args:
        content: The AI reply content, may contain [INTERACTIVE]...[/INTERACTIVE] blocks.

    Returns:
        ParsedContent with text segments and interactive blocks.
    """
    text_segments = []
    interactive_blocks = []

    pos = 0
    while pos < len(content):
        # Find next interactive block start
        match = _INTERACTIVE_START_RE.search(content, pos)
        if not match:
            # No more blocks, add remaining text
            remaining = content[pos:].strip()
            if remaining:
                text_segments.append(remaining)
            break

        # Add text before this block
        before = content[pos:match.start()].strip()
        if before:
            text_segments.append(before)

        # Parse block attributes
        block_type = match.group("type")
        block_id = match.group("id")
        attrs_str = match.group("attrs")
        attrs = dict(_ATTR_RE.findall(attrs_str))

        # Find block end
        end_match = _INTERACTIVE_END_RE.search(content, match.end())
        if not end_match:
            # Malformed block, treat as text
            text_segments.append(content[match.start():])
            break

        # Parse block content (between start and end)
        block_content = content[match.end():end_match.start()]

        # Parse options if present
        options = []
        for opt_match in _OPTION_RE.finditer(block_content):
            opt_attrs = dict(_ATTR_RE.findall(opt_match.group("attrs")))
            options.append({
                "id": opt_match.group("id"),
                "label": opt_match.group("label"),
                "icon": opt_attrs.get("icon", ""),
                "style": opt_attrs.get("style", "default"),
            })

        # Create block
        block = InteractiveBlock(
            block_type=block_type,
            block_id=block_id,
            message=attrs.get("message", ""),
            options=options,
            attrs=attrs,
        )
        interactive_blocks.append(block)

        pos = end_match.end()

    return ParsedContent(text_segments, interactive_blocks)


def has_interactive_markup(content: str) -> bool:
    """Check if content contains interactive markup."""
    return _INTERACTIVE_START_RE.search(content) is not None


# ---- Feishu card rendering ----

def render_interactive_to_feishu_card(
    block: InteractiveBlock,
    *,
    title: str = "Claw AI 助手",
    scene: str = "default",
) -> dict[str, Any]:
    """Render an InteractiveBlock to Feishu card v2 format.

    Args:
        block: The interactive block to render.
        title: Card header title.
        scene: Scene type for color.

    Returns:
        Feishu card v2 JSON dict.
    """
    elements = []

    # Add message if present
    if block.message:
        elements.append({
            "tag": "markdown",
            "content": block.message,
        })

    # Render based on block type
    if block.block_type == "confirm":
        # Confirm: Yes/No buttons
        elements.append({
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"content": "确认", "tag": "lark_md"},
                    "type": "primary",
                    "value": {
                        "action": "confirm_yes",
                        "interactive_id": block.block_id,
                        "interactive_type": block.block_type,
                    },
                },
                {
                    "tag": "button",
                    "text": {"content": "取消", "tag": "lark_md"},
                    "type": "default",
                    "value": {
                        "action": "confirm_no",
                        "interactive_id": block.block_id,
                        "interactive_type": block.block_type,
                    },
                },
            ],
        })

    elif block.block_type == "select":
        # Select: Option buttons
        actions = []
        for opt in block.options:
            actions.append({
                "tag": "button",
                "text": {"content": opt["label"], "tag": "lark_md"},
                "type": "primary" if opt["style"] == "primary" else "default",
                "value": {
                    "action": f"select_{opt['id']}",
                    "interactive_id": block.block_id,
                    "interactive_type": block.block_type,
                    "selected_id": opt["id"],
                    "selected_label": opt["label"],
                },
            })
        if actions:
            elements.append({"tag": "action", "actions": actions})

    elif block.block_type == "input":
        # Input: Show hint as note
        hint = block.attrs.get("hint", "")
        unit = block.attrs.get("unit", "")
        if hint or unit:
            hint_text = f"提示: {hint}"
            if unit:
                hint_text += f" (单位: {unit})"
            elements.append({
                "tag": "note",
                "elements": [{"tag": "plain_text", "content": hint_text}],
            })
        # Input is handled by user typing, no buttons needed
        # But we add a placeholder note
        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": "请直接回复输入内容"}],
        })

    elif block.block_type == "multi_step":
        # Multi-step: Show progress + options
        step = block.attrs.get("step", "1")
        total = block.attrs.get("total", "1")
        progress = f"步骤 {step}/{total}"
        elements.append({
            "tag": "markdown",
            "content": f"**{progress}**",
        })
        # Render options like select
        actions = []
        for opt in block.options:
            actions.append({
                "tag": "button",
                "text": {"content": opt["label"], "tag": "lark_md"},
                "type": "default",
                "value": {
                    "action": f"step_{step}_{opt['id']}",
                    "interactive_id": block.block_id,
                    "interactive_type": block.block_type,
                    "selected_id": opt["id"],
                    "step": step,
                },
            })
        if actions:
            elements.append({"tag": "action", "actions": actions})

    # Build card
    _SCENE_TEMPLATES = {
        "default": "blue",
        "control": "green",
        "alert": "red",
        "confirm": "orange",
    }

    return {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"content": title, "tag": "plain_text"},
            "template": _SCENE_TEMPLATES.get(scene, "blue"),
        },
        "body": {"elements": elements},
    }


# ---- Callback message builders ----

def build_interactive_callback(
    action: str,
    interactive_id: str,
    selected_id: str = "",
    selected_label: str = "",
    user_input: str = "",
) -> str:
    """Build callback message to send back to AI.

    Args:
        action: The action type (confirm_yes, confirm_no, select_xxx, etc.)
        interactive_id: The interactive block ID.
        selected_id: Selected option ID (for select/multi_step).
        selected_label: Selected option label.
        user_input: User input text (for input type).

    Returns:
        Natural language callback message.
    """
    if action == "confirm_yes":
        return f"[用户点击了「确认」按钮，交互ID: {interactive_id}]"
    elif action == "confirm_no":
        return f"[用户点击了「取消」按钮，交互ID: {interactive_id}]"
    elif action.startswith("select_"):
        return f"[用户选择了「{selected_label}」({selected_id})，交互ID: {interactive_id}]"
    elif action.startswith("step_"):
        return f"[用户在步骤中选择了「{selected_label}」({selected_id})，交互ID: {interactive_id}]"
    elif user_input:
        return f"[用户输入了「{user_input}」，交互ID: {interactive_id}]"
    else:
        return f"[用户交互: {action}，交互ID: {interactive_id}]"


# ---- Integration helpers ----

async def process_interactive_reply(
    content: str,
    *,
    title: str = "Claw AI 助手",
    scene: str = "default",
) -> list[dict]:
    """Process a reply containing interactive markup.

    Returns a list of items to send:
    - For text segments: {"type": "text", "content": "..."}
    - For interactive blocks: {"type": "interactive", "card": {...}, "block": InteractiveBlock}

    Args:
        content: The AI reply content.
        title: Card title.
        scene: Scene type.

    Returns:
        List of items to send.
    """
    parsed = parse_interactive_markup(content)
    results = []

    # Interleave text and interactive blocks
    text_idx = 0
    block_idx = 0

    # Simple approach: text first, then interactive
    for text in parsed.text_segments:
        if text.strip():
            results.append({"type": "text", "content": text})

    for block in parsed.interactive_blocks:
        card = render_interactive_to_feishu_card(block, title=title, scene=scene)
        results.append({
            "type": "interactive",
            "card": card,
            "block": block,
        })

    return results
