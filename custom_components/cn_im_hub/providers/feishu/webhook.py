from __future__ import annotations

import json
import logging
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from ...const import DOMAIN

_LOGGER = logging.getLogger(__name__)


class FeishuCardCallbackView(HomeAssistantView):
    requires_auth = False
    url = "/api/cn_im_hub/feishu/card_callback"
    name = "api:cn_im_hub:feishu:card_callback"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        try:
            data = await _read_body(request)
            if data.get("type") == "url_verification":
                return web.json_response({"challenge": data.get("challenge", "")})
            if not data.get("token", ""):
                return web.json_response({"error": "unauthorized"}, status=401)

            event = data.get("event", {})
            action = event.get("action", {})
            operator = event.get("operator", {})
            action_value = _decode_action_value(action.get("value", {}))
            self._hass.bus.async_fire(f"{DOMAIN}_feishu_card_action", {"action": action, "operator": operator, "raw_data": data})
            _LOGGER.info(
                "Feishu card action fired: value=%s, operator=%s",
                json.dumps(action_value, ensure_ascii=False)[:300],
                json.dumps(operator, ensure_ascii=False)[:200],
            )
            return web.json_response({"toast": {"type": "info", "content": _toast_content(action_value)}})
        except Exception:
            _LOGGER.exception("Feishu card callback error")
            return web.json_response({"error": "internal error"}, status=400)

    async def get(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "message": "Feishu card callback endpoint is active"})


async def _read_body(request: web.Request) -> dict[str, Any]:
    try:
        return json.loads(await request.text())
    except json.JSONDecodeError:
        _LOGGER.warning("Feishu card callback: invalid JSON body")
        return {}


def _decode_action_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}


def _toast_content(action_value: Any) -> str:
    if not isinstance(action_value, dict):
        return "OK"
    template = action_value.get("toast")
    if not template:
        return "OK"
    try:
        return template.format(**action_value)
    except (KeyError, IndexError, ValueError):
        return template
