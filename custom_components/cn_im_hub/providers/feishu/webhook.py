from __future__ import annotations

import json
import logging
from json import JSONDecodeError
from typing import Any

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from ...const import CONF_FEISHU_VERIFICATION_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)


class FeishuCardCallbackView(HomeAssistantView):
    requires_auth = False
    url = "/api/cn_im_hub/feishu/card_callback"
    name = "api:cn_im_hub:feishu:card_callback"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    def _get_verification_token(self) -> str:
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            for subentry in entry.subentries.values():
                if subentry.subentry_type == "feishu":
                    token = subentry.data.get(CONF_FEISHU_VERIFICATION_TOKEN, "").strip()
                    if token:
                        return token
        return ""

    async def post(self, request: web.Request) -> web.Response:
        try:
            raw = await request.text()

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                _LOGGER.warning("Feishu card callback: invalid JSON body")
                return web.json_response({})

            if data.get("type") == "url_verification":
                challenge = data.get("challenge", "")
                _LOGGER.info("Feishu URL verification: challenge=%s", challenge)
                return web.json_response({"challenge": challenge})

            callback_token = data.get("token", "") or (data.get("header", {}) or {}).get("token", "")

            configured_token = self._get_verification_token()
            if configured_token:
                if not callback_token or callback_token != configured_token:
                    _LOGGER.warning(
                        "Feishu card callback: token mismatch (got=%s), rejecting",
                        callback_token[:16] if callback_token else "(empty)",
                    )
                    return web.json_response({"error": "unauthorized"}, status=401)

            event = data.get("event", {})
            action = event.get("action", {})
            operator = event.get("operator", {})
            action_value = _decode_action_value(action.get("value", {}))
            from_ai = bool(isinstance(action_value, dict) and action_value.get("from_ai"))

            if from_ai:
                await self._handle_claw_card_action(event, action_value, operator)
            else:
                self._hass.bus.async_fire(
                    f"{DOMAIN}_feishu_card_action",
                    {"action": action, "operator": operator, "raw_data": data, "from_ai": False},
                )
            _LOGGER.info(
                "Feishu card action: value=%s, operator=%s, from_ai=%s",
                json.dumps(action_value, ensure_ascii=False)[:300],
                json.dumps(operator, ensure_ascii=False)[:200],
                from_ai,
            )

            toast_content = _toast_content(action_value)
            return web.json_response({"toast": {"type": "info", "content": toast_content}})
        except Exception:
            _LOGGER.exception("Feishu card callback error")
            return web.json_response({"error": "internal error"}, status=400)

    async def get(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "message": "Feishu card callback endpoint is active"})

    async def _handle_claw_card_action(self, event: dict, action_value: Any, operator: dict) -> None:
        action_text = action_value.get("action", "") if isinstance(action_value, dict) else ""
        if not action_text:
            return

        agent_id = self._hass.data.get(DOMAIN, {}).get("agent_id", "")
        if not agent_id:
            _LOGGER.warning("Cannot route card action: no agent_id configured")
            return

        chat_id = (event.get("context") or {}).get("open_chat_id", "")
        if not chat_id:
            return

        api_clients = self._hass.data.get(DOMAIN, {}).get("_feishu_api_clients", {})
        if not api_clients:
            return

        api = next(iter(api_clients.values()))
        receive_type = "chat_id"
        user_id = operator.get("open_id", "")

        async def _route_and_reply() -> None:
            try:
                from ...core.command import execute_command, parse_command
                command = parse_command(action_text)
                if command is None:
                    return
                result = await execute_command(
                    self._hass, command,
                    conversation_id=f"feishu:{chat_id}",
                    agent_id=agent_id,
                    user_id=user_id,
                )
                if not result:
                    return
                reply = str(result)
                from .card import build_response_card, mark_claw
                from ...media.rich_media import extract_reply_prefix
                prefix_name, reply_body = extract_reply_prefix(reply)
                card = mark_claw(build_response_card(reply_body, title=prefix_name or "Claw Assistant"))
                await api.async_send_card_message(
                    receive_id=chat_id,
                    card=card,
                    receive_id_type=receive_type,
                )
            except Exception:
                _LOGGER.exception("Failed to route card action back to agent")

        self._hass.async_create_background_task(
            _route_and_reply(),
            "cn_im_hub_card_action_route",
        )


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
