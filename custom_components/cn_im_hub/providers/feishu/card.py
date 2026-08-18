from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...media.card import CardSpec

_CARD_TEMPLATE_LENGTH_THRESHOLD = 300
_REPLY_MAX_LENGTH = 1800
_CARD_PAGE_MAX_LENGTH = 1500

_CARD_SYMBOLS = ("\n1.", "##", "###")

_FEISHU_COLOR_MAP = {
    0: "default",
    1: "primary",
    2: "primary",
    3: "danger",
    4: "primary",
}

# Progress card header colors cycle
_PROGRESS_COLORS = ("blue", "wathet", "turquoise", "green", "yellow", "orange", "red")


def should_send_as_card(text: str) -> bool:
    return (
        len(text) > _CARD_TEMPLATE_LENGTH_THRESHOLD
        or any(s in text for s in _CARD_SYMBOLS)
    )


def _build_header(title: str, template: str = "blue") -> dict:
    return {
        "title": {"tag": "plain_text", "content": title or "Claw Assistant"},
        "template": template,
    }


def _build_markdown(text: str) -> dict:
    return {"tag": "markdown", "content": text[:_REPLY_MAX_LENGTH]}


def build_divider() -> dict:
    """Return a divider / horizontal rule element."""
    return {"tag": "hr"}


def build_note(text: str) -> dict:
    """Return a note element."""
    return {
        "tag": "note",
        "elements": [{"tag": "plain_text", "content": text[:200]}],
    }


def build_response_card(text: str, *, title: str = "") -> dict:
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": _build_header(title or "Claw Assistant"),
        "body": {
            "elements": [_build_markdown(text)],
        },
    }


def build_feishu_card(spec: CardSpec, *, title: str = "") -> dict:
    elements: list[dict] = []
    if spec.text:
        elements.append(_build_markdown(spec.text))
    for row in spec.rows:
        columns: list[dict] = []
        for btn in row.buttons:
            color = _FEISHU_COLOR_MAP.get(btn.style, "default")
            columns.append({
                "tag": "column",
                "width": "weighted",
                "weight": 1,
                "elements": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": btn.label},
                    "type": color,
                    "width": "fill",
                    "value": {"action": btn.data},
                }],
            })
        if columns:
            elements.append({
                "tag": "column_set",
                "flex_mode": "bisect",
                "columns": columns,
            })
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": _build_header(title or "Claw Assistant"),
        "body": {"elements": elements},
    }


def build_paginated_cards(text: str, *, title: str = "", max_length: int = _CARD_PAGE_MAX_LENGTH) -> list[dict]:
    """Split long text into multiple cards with page indicator.

    Returns a list of card dicts, each with a page indicator in the footer.
    If text fits in one page, returns a single card without page indicator.
    """
    if len(text) <= max_length:
        return [build_response_card(text, title=title)]

    pages: list[str] = []
    while text:
        if len(text) <= max_length:
            pages.append(text)
            break
        # Try to split at a newline boundary
        split_at = text.rfind("\n", 0, max_length)
        if split_at <= 0:
            split_at = max_length
        pages.append(text[:split_at])
        text = text[split_at:].strip()

    cards: list[dict] = []
    total = len(pages)
    for i, page in enumerate(pages, 1):
        page_indicator = f"--- {i}/{total} ---"
        elements: list[dict] = [_build_markdown(page)]
        if total > 1:
            elements.append(build_divider())
            elements.append(build_note(page_indicator))
        cards.append({
            "schema": "2.0",
            "config": {"update_multi": True},
            "header": _build_header(title or "Claw Assistant"),
            "body": {"elements": elements},
        })
    return cards


def build_progress_card(text: str, *, title: str = "", cycle: int = 0) -> dict:
    """Build a progress card with a cycling header color.

    The cycle parameter changes the header color across updates to provide
    visual feedback that the card is being updated.
    """
    color = _PROGRESS_COLORS[cycle % len(_PROGRESS_COLORS)]
    elements: list[dict] = [_build_markdown(text)]
    elements.append(build_note("⏳ 处理中..."))
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": _build_header(title or "Claw Assistant", template=color),
        "body": {"elements": elements},
    }


def build_final_card(text: str, *, title: str = "") -> dict:
    """Build a final result card (green header, no progress indicator)."""
    elements: list[dict] = [_build_markdown(text)]
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": _build_header(title or "Claw Assistant", template="green"),
        "body": {"elements": elements},
    }


def mark_claw(card: dict) -> dict:
    """Inject from_ai into all button values so callbacks route back to Claw AI."""
    def _walk(node):
        if isinstance(node, dict):
            if node.get("tag") == "button" and isinstance(node.get("value"), dict):
                node["value"]["from_ai"] = True
            for v in node.values():
                _walk(v)
        elif isinstance(node, list):
            for v in node:
                _walk(v)
    _walk(card)
    return card