from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...media.card import CardSpec

_CARD_TEMPLATE_LENGTH_THRESHOLD = 300
_REPLY_MAX_LENGTH = 1800

_CARD_SYMBOLS = ("\n1.", "##", "###")

_FEISHU_COLOR_MAP = {
    0: "default",
    1: "primary",
    2: "primary",
    3: "danger",
    4: "primary",
}


def should_send_as_card(text: str) -> bool:
    return (
        len(text) > _CARD_TEMPLATE_LENGTH_THRESHOLD
        or any(s in text for s in _CARD_SYMBOLS)
    )


def build_response_card(text: str, *, title: str = "") -> dict:
    return {
        "schema": "2.0",
        "config": {"update_multi": True},
        "header": {
            "title": {"tag": "plain_text", "content": title or "Claw Assistant"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": text[:_REPLY_MAX_LENGTH]},
            ],
        },
    }


def build_feishu_card(spec: CardSpec, *, title: str = "") -> dict:
    elements: list[dict] = []
    if spec.text:
        elements.append({"tag": "markdown", "content": spec.text[:_REPLY_MAX_LENGTH]})
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
        "header": {
            "title": {"tag": "plain_text", "content": title or "Claw Assistant"},
            "template": "blue",
        },
        "body": {
            "elements": elements,
        },
    }
