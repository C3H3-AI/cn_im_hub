from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

_CARD_JSON_RE = re.compile(r"^\{.*\}$", re.DOTALL)

_STYLE_MAP: dict[str, int] = {
    "gray": 0, "grey": 0,
    "blue": 1,
    "recommend": 2,
    "red": 3,
    "primary": 4,
    "0": 0, "1": 1, "2": 2, "3": 3, "4": 4,
}
_STYLE_SUFFIX_RE = re.compile(r"^(.+?)@(\w+)$")


@dataclass(slots=True)
class CardButton:
    label: str
    data: str
    style: int = 1
    visited_label: str = ""


@dataclass(slots=True)
class CardRow:
    buttons: list[CardButton] = field(default_factory=list)


@dataclass(slots=True)
class CardSpec:
    text: str
    rows: list[CardRow] = field(default_factory=list)
    card_id: str = ""


def parse_card_source(source: str) -> CardSpec | None:
    source = source.strip()
    if not source:
        return None
    if _CARD_JSON_RE.match(source):
        return _parse_json_card(source)
    return _parse_simple_card(source)


def _parse_json_card(source: str) -> CardSpec | None:
    try:
        data = json.loads(source)
    except (json.JSONDecodeError, ValueError):
        return None
    text = str(data.get("text", ""))
    card_id = str(data.get("id", ""))
    rows: list[CardRow] = []
    for row_data in data.get("rows", data.get("buttons", [])):
        if isinstance(row_data, list):
            buttons = [_parse_button(b) for b in row_data if isinstance(b, (dict, str))]
        elif isinstance(row_data, dict) and "buttons" in row_data:
            buttons = [_parse_button(b) for b in row_data["buttons"] if isinstance(b, (dict, str))]
        elif isinstance(row_data, (dict, str)):
            buttons = [_parse_button(row_data)]
        else:
            continue
        buttons = [b for b in buttons if b is not None]
        if buttons:
            rows.append(CardRow(buttons=buttons))
    return CardSpec(text=text, rows=rows, card_id=card_id) if rows else None


def _parse_button(item: dict | str) -> CardButton | None:
    if isinstance(item, str):
        return CardButton(label=item, data=item)
    label = str(item.get("label", item.get("text", "")))
    data = str(item.get("data", item.get("value", label)))
    style = int(item.get("style", 1))
    visited = str(item.get("visited_label", item.get("visited", f"已选: {label}")))
    return CardButton(label=label, data=data, style=style, visited_label=visited) if label else None


def _parse_simple_button(raw: str) -> CardButton | None:
    raw = raw.strip()
    if not raw:
        return None
    style = 1
    m = _STYLE_SUFFIX_RE.match(raw)
    if m:
        raw, color = m.group(1).strip(), m.group(2).strip().lower()
        style = _STYLE_MAP.get(color, 1)
    if "=" in raw:
        label, data = raw.split("=", 1)
        return CardButton(label=label.strip(), data=data.strip(), style=style)
    return CardButton(label=raw, data=raw, style=style)


def _parse_simple_card(source: str) -> CardSpec | None:
    lines = source.strip().split("|")
    if len(lines) < 2:
        return None
    text = lines[0].strip()
    rows: list[CardRow] = []
    for part in lines[1:]:
        buttons = [_parse_simple_button(b) for b in part.split(",")]
        buttons = [b for b in buttons if b is not None]
        if buttons:
            rows.append(CardRow(buttons=buttons))
    return CardSpec(text=text, rows=rows) if rows else None


_MAX_KEYBOARD_ROWS = 5
_MAX_BUTTONS_PER_ROW = 5


def build_inline_keyboard(spec: CardSpec) -> dict[str, Any]:
    rows = []
    for i, row in enumerate(spec.rows[:_MAX_KEYBOARD_ROWS]):
        buttons = []
        for j, btn in enumerate(row.buttons[:_MAX_BUTTONS_PER_ROW]):
            buttons.append({
                "id": f"card_{i}_{j}",
                "render_data": {
                    "label": btn.label,
                    "visited_label": btn.visited_label or f"已选: {btn.label}",
                    "style": btn.style,
                },
                "action": {
                    "type": 1,
                    "data": f"card:{spec.card_id}:{btn.data}" if spec.card_id else f"card::{btn.data}",
                    "permission": {"type": 2},
                    "click_limit": 1,
                },
                "group_id": "card_select",
            })
        if buttons:
            rows.append({"buttons": buttons})
    return {"content": {"rows": rows}}
