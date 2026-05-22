"""Feishu provider implementation."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from json import JSONDecodeError
from typing import Any

import aiohttp
import voluptuous as vol
from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..command import execute_command, parse_command
from ..const import (
    CONF_FEISHU_APP_ID,
    CONF_FEISHU_APP_SECRET,
    DEFAULT_FEISHU_TARGET_TYPE,
    DOMAIN,
    PROVIDER_FEISHU,
)
from ..known_targets import async_get_tracker
from ..models import ProviderRuntime
from .base import ProviderSpec

_LOGGER = logging.getLogger(__name__)
_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
_REPLY_MAX_LENGTH = 1800
_CARD_TEMPLATE_LENGTH_THRESHOLD = 100


def _should_send_as_card(text: str) -> bool:
    if len(text) > _CARD_TEMPLATE_LENGTH_THRESHOLD:
        return True
    if any(keyword in text for keyword in ["打开", "关闭", "控制", "设置", "调整", "开启", "停止"]):
        return True
    if any(symbol in text for symbol in ["\n-", "\n•", "\n*", "\n1.", "##", "###"]):
        return True
    return False


def _build_response_card(text: str) -> dict:
    return {
        "header": {
            "title": {"content": "Claw AI 助手", "tag": "plain_text"},
            "template": "blue",
        },
        "elements": [
            {
                "tag": "div",
                "text": {"content": text[:_REPLY_MAX_LENGTH], "tag": "plain_text"},
            },
        ],
    }


def _import_lark() -> tuple[Any, Any]:
    import lark_oapi as lark
    import lark_oapi.ws.client as lark_ws_client

    return lark, lark_ws_client


class FeishuApiClient:
    """Wrapper around Feishu HTTP and SDK APIs."""

    def __init__(self, hass: HomeAssistant, app_id: str, app_secret: str) -> None:
        self._hass = hass
        self._app_id = app_id
        self._app_secret = app_secret
        self._session = async_get_clientsession(hass)

    async def async_validate_connection(self) -> None:
        await self.async_get_tenant_access_token()

    async def async_get_tenant_access_token(self) -> str:
        payload = {"app_id": self._app_id, "app_secret": self._app_secret}
        async with asyncio.timeout(15):
            response = await self._session.post(_TOKEN_URL, json=payload)
        data = await _async_read_json(response)
        if response.status != 200 or data.get("code") != 0:
            raise RuntimeError(f"token request failed: {data.get('msg', response.reason)}")
        token = data.get("tenant_access_token")
        if not token:
            raise RuntimeError("token request succeeded but token missing")
        return token

    async def async_send_text_message(
        self,
        *,
        receive_id: str,
        text: str,
        receive_id_type: str = DEFAULT_FEISHU_TARGET_TYPE,
    ) -> None:
        text = text[:_REPLY_MAX_LENGTH]
        content = json.dumps({"text": text}, ensure_ascii=False)

        def _send() -> None:
            lark, _ = _import_lark()
            client = (
                lark.Client.builder()
                .app_id(self._app_id)
                .app_secret(self._app_secret)
                .log_level(lark.LogLevel.INFO)
                .build()
            )
            request = (
                lark.im.v1.CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    lark.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("text")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = client.im.v1.message.create(request)
            if not response.success():
                raise RuntimeError(
                    f"send message failed code={response.code}, msg={response.msg}, log_id={response.get_log_id()}"
                )

        await self._hass.async_add_executor_job(_send)

    async def async_send_image_message(
        self,
        *,
        receive_id: str,
        image_bytes: bytes,
        receive_id_type: str = DEFAULT_FEISHU_TARGET_TYPE,
    ) -> None:
        if not image_bytes:
            raise ValueError("Feishu image data is empty")
        token = await self.async_get_tenant_access_token()
        form = aiohttp.FormData()
        form.add_field("image_type", "message")
        form.add_field("image", image_bytes, filename="camera.jpg", content_type="image/jpeg")
        async with asyncio.timeout(30):
            response = await self._session.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Authorization": f"Bearer {token}"},
                data=form,
            )
        data = await _async_read_json(response)
        if response.status != 200 or data.get("code") != 0:
            raise RuntimeError(f"upload image failed: {data.get('msg', response.reason)}")
        image_key = str((data.get("data") or {}).get("image_key") or "")
        if not image_key:
            raise RuntimeError("upload image succeeded but image_key missing")

        content = json.dumps({"image_key": image_key}, ensure_ascii=False)

        def _send() -> None:
            lark, _ = _import_lark()
            client = (
                lark.Client.builder()
                .app_id(self._app_id)
                .app_secret(self._app_secret)
                .log_level(lark.LogLevel.INFO)
                .build()
            )
            request = (
                lark.im.v1.CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    lark.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("image")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = client.im.v1.message.create(request)
            if not response.success():
                raise RuntimeError(
                    f"send image failed code={response.code}, msg={response.msg}, log_id={response.get_log_id()}"
                )

        await self._hass.async_add_executor_job(_send)

    async def async_send_safe_reply(self, *, receive_id: str, text: str, receive_id_type: str) -> None:
        try:
            await self.async_send_text_message(
                receive_id=receive_id,
                text=text,
                receive_id_type=receive_id_type,
            )
        except Exception as err:
            _LOGGER.warning("Failed to send message back to Feishu: %s", err)

    async def async_send_card_message(
        self,
        *,
        receive_id: str,
        card: dict[str, Any],
        receive_id_type: str = DEFAULT_FEISHU_TARGET_TYPE,
    ) -> None:
        content = json.dumps(card, ensure_ascii=False)

        def _send() -> None:
            lark, _ = _import_lark()
            client = (
                lark.Client.builder()
                .app_id(self._app_id)
                .app_secret(self._app_secret)
                .log_level(lark.LogLevel.INFO)
                .build()
            )
            request = (
                lark.im.v1.CreateMessageRequest.builder()
                .receive_id_type(receive_id_type)
                .request_body(
                    lark.im.v1.CreateMessageRequestBody.builder()
                    .receive_id(receive_id)
                    .msg_type("interactive")
                    .content(content)
                    .build()
                )
                .build()
            )
            response = client.im.v1.message.create(request)
            if not response.success():
                raise RuntimeError(
                    f"send card failed code={response.code}, msg={response.msg}, log_id={response.get_log_id()}"
                )

        await self._hass.async_add_executor_job(_send)

    async def async_upload_image(self, image_data: bytes) -> str | None:
        token = await self.async_get_tenant_access_token()
        form = aiohttp.FormData()
        form.add_field("image_type", "message")
        form.add_field("image", image_data, filename="snapshot.jpg", content_type="image/jpeg")
        async with asyncio.timeout(30):
            upload_resp = await self._session.post(
                "https://open.feishu.cn/open-apis/im/v1/images",
                headers={"Authorization": f"Bearer {token}"},
                data=form,
            )
        upload_data = await _async_read_json(upload_resp)
        if upload_resp.status != 200 or upload_data.get("code") != 0:
            _LOGGER.warning("Failed to upload image to Feishu: %s", upload_data.get("msg"))
            return None
        return str((upload_data.get("data") or {}).get("image_key") or "")


async def async_inject_camera_snapshot(
    hass: HomeAssistant,
    card: dict[str, Any],
    image_data: bytes,
    ws_client: Any,
) -> bool:
    app_id = getattr(ws_client, "_app_id", "")
    app_secret = getattr(ws_client, "_app_secret", "")
    if not app_id or not app_secret:
        return False

    feishu_api = FeishuApiClient(hass, app_id, app_secret)
    image_key = await feishu_api.async_upload_image(image_data)
    if not image_key:
        return False

    elements = card.get("elements", [])
    elements.insert(
        next((i for i, e in enumerate(elements) if e.get("tag") == "hr"), 0),
        {"tag": "img", "img_key": image_key, "alt": {"tag": "plain_text", "content": "摄像头截图"}},
    )
    card["elements"] = elements
    return True


class FeishuWsClient:
    """Manage Feishu websocket lifecycle and callbacks."""

    def __init__(
        self,
        *,
        hass: HomeAssistant,
        app_id: str,
        app_secret: str,
        message_handler: Callable[[dict[str, str]], Awaitable[None]],
    ) -> None:
        self._hass = hass
        self._app_id = app_id
        self._app_secret = app_secret
        self._message_handler = message_handler
        self._client: object | None = None
        self._runner_task: asyncio.Task | None = None
        self._seen_message_ids: OrderedDict[str, None] = OrderedDict()
        self._seen_limit = 512
        self._status = "disconnected"

    @property
    def status(self) -> str:
        return self._status

    async def async_start(self) -> None:
        if self._runner_task is not None:
            return
        self._runner_task = self._hass.async_create_background_task(
            self._async_run_forever(),
            "cn_im_hub_feishu_ws_runner",
        )

    async def async_stop(self) -> None:
        if self._runner_task is not None:
            self._runner_task.cancel()
            try:
                await self._runner_task
            except asyncio.CancelledError:
                pass
            self._runner_task = None
        await self._hass.async_add_executor_job(self._stop_sync)
        self._status = "disconnected"

    async def _async_run_forever(self) -> None:
        while True:
            self._status = "connecting"
            try:
                await self._hass.async_add_executor_job(self._start_sync)
            except asyncio.CancelledError:
                raise
            except Exception as err:
                self._status = "error"
                _LOGGER.warning("Feishu websocket error: %s", err)
            self._status = "disconnected"
            await asyncio.sleep(5)

    def _start_sync(self) -> None:
        lark, lark_ws_client = _import_lark()
        if lark_ws_client.loop.is_running():
            worker_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(worker_loop)
            lark_ws_client.loop = worker_loop

        builder = lark.EventDispatcherHandler.builder("", "")
        event_handler = builder.register_p2_customized_event(
            "im.message.receive_v1",
            self._on_custom_message_sync,
        ).register_p2_customized_event(
            "im.message.message_read_v1",
            self._on_ignored_event_sync,
        ).build()

        self._client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )
        self._status = "connected"
        self._client.start()

    def _stop_sync(self) -> None:
        client = self._client
        self._client = None
        if client is not None:
            stop = getattr(client, "stop", None)
            if callable(stop):
                stop()

    def _on_custom_message_sync(self, data: object) -> None:
        event = getattr(data, "event", None)
        if not isinstance(event, dict):
            return

        message = event.get("message") or {}
        sender = event.get("sender") or {}

        message_id = str(message.get("message_id") or "")
        if not message_id or message_id in self._seen_message_ids:
            return

        self._seen_message_ids[message_id] = None
        self._seen_message_ids.move_to_end(message_id)
        if len(self._seen_message_ids) > self._seen_limit:
            self._seen_message_ids.popitem(last=False)

        content_raw = str(message.get("content") or "")
        text = _extract_text(content_raw)
        chat_id = str(message.get("chat_id") or "")

        sender_id_obj = sender.get("sender_id") if isinstance(sender, dict) else None
        user_id = ""
        if isinstance(sender_id_obj, dict):
            user_id = str(
                sender_id_obj.get("open_id")
                or sender_id_obj.get("user_id")
                or sender_id_obj.get("union_id")
                or ""
            )

        future = asyncio.run_coroutine_threadsafe(
            self._message_handler(
                {
                    "message_id": message_id,
                    "text": text,
                    "chat_id": chat_id,
                    "user_id": user_id,
                }
            ),
            self._hass.loop,
        )
        future.add_done_callback(_log_future_exception)

    def _on_ignored_event_sync(self, _: object) -> None:
        return


def _extract_text(content: str) -> str:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content.strip()

    if isinstance(payload, dict):
        return str(payload.get("text", "")).strip()
    return ""


def _log_future_exception(future: asyncio.Future) -> None:
    try:
        future.result()
    except Exception:
        _LOGGER.exception("Failed to process Feishu message")


async def _async_read_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        data = await response.json(content_type=None)
    except (aiohttp.ContentTypeError, JSONDecodeError) as err:
        body = await response.text()
        data = _parse_json_from_text(body)
        if data is None:
            raise RuntimeError(f"invalid json response status={response.status}") from err

    if not isinstance(data, dict):
        raise RuntimeError(f"invalid response payload type: {type(data).__name__}")
    return data


def _parse_json_from_text(body: str) -> dict[str, Any] | None:
    body = body.strip()
    if not body:
        return None
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else None
    except JSONDecodeError:
        pass

    start = body.find("{")
    end = body.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        data = json.loads(body[start : end + 1])
    except JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


async def async_validate_config(hass: HomeAssistant, config: dict[str, Any]) -> None:
    app_id = str(config.get(CONF_FEISHU_APP_ID, "")).strip()
    app_secret = str(config.get(CONF_FEISHU_APP_SECRET, "")).strip()
    if not app_id or not app_secret:
        raise ValueError("app_id and app_secret are required")
    client = FeishuApiClient(hass, app_id, app_secret)
    await client.async_validate_connection()


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, Any],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    """Create runtime for Feishu provider."""
    app_id = str(config.get(CONF_FEISHU_APP_ID, "")).strip()
    app_secret = str(config.get(CONF_FEISHU_APP_SECRET, "")).strip()

    api_client = FeishuApiClient(hass, app_id, app_secret)
    await api_client.async_validate_connection()
    tracker = await async_get_tracker(hass, subentry_id)

    async def _handle_message(message: dict[str, str]) -> None:
        chat_id = message.get("chat_id", "")
        user_id = message.get("user_id", "")
        text = message.get("text", "").strip()
        receive_id = chat_id or user_id
        receive_type = "chat_id" if chat_id else "open_id"
        if not receive_id or not text:
            return
        await tracker.async_record(
            provider=PROVIDER_FEISHU,
            target=receive_id,
            target_type=receive_type,
            display_name=user_id or chat_id,
        )

        async def _reply(reply_text: str, reply_card: dict[str, Any] | None = None) -> None:
            if reply_card:
                try:
                    await api_client.async_send_card_message(
                        receive_id=receive_id,
                        card=reply_card,
                        receive_id_type=receive_type,
                    )
                    return
                except Exception as err:
                    _LOGGER.warning("Card send failed, falling back to text: %s", err)
            await api_client.async_send_safe_reply(
                receive_id=receive_id,
                receive_id_type=receive_type,
                text=reply_text,
            )

        try:
            command = parse_command(text)
        except ValueError as err:
            await _reply(f"Invalid command: {err}")
            return

        if command is None:
            return

        try:
            result = await execute_command(
                hass,
                command,
                conversation_id=f"feishu:{receive_id}",
                agent_id=agent_id,
            )
        except Exception as err:
            result = f"Execution failed: {type(err).__name__}"
            _LOGGER.exception("Feishu command execution failed: %s", err)

        result_str = str(result)
        if _should_send_as_card(result_str):
            card = _build_response_card(result_str)
            await _reply(result_str, card)
        else:
            await _reply(result_str)

    ws_client = FeishuWsClient(
        hass=hass,
        app_id=app_id,
        app_secret=app_secret,
        message_handler=_handle_message,
    )
    await ws_client.async_start()

    async def _send(target: str, message: str, target_type: str) -> None:
        await api_client.async_send_text_message(
            receive_id=target,
            text=message,
            receive_id_type=target_type or DEFAULT_FEISHU_TARGET_TYPE,
        )

    async def _send_image(target: str, image_bytes: bytes, target_type: str) -> None:
        await api_client.async_send_image_message(
            receive_id=target,
            image_bytes=image_bytes,
            receive_id_type=target_type or DEFAULT_FEISHU_TARGET_TYPE,
        )

    async def _send_card(target: str, card: dict[str, Any], target_type: str) -> None:
        await api_client.async_send_card_message(
            receive_id=target,
            card=card,
            receive_id_type=target_type or DEFAULT_FEISHU_TARGET_TYPE,
        )

    return ProviderRuntime(
        key=PROVIDER_FEISHU,
        title="Feishu",
        subentry_id=subentry_id,
        client=ws_client,
        stop=ws_client.async_stop,
        send_text=_send,
        status=lambda: ws_client.status,
        known_targets=tracker.snapshot,
        selected_target=tracker.selected_target,
        select_target=tracker.async_select_target,
        send_image=_send_image,
        send_card=_send_card,
    )


def _build_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_FEISHU_APP_ID, default=current.get(CONF_FEISHU_APP_ID, "")): str,
            vol.Required(CONF_FEISHU_APP_SECRET, default=current.get(CONF_FEISHU_APP_SECRET, "")): str,
        }
    )


PROVIDER_SPEC = ProviderSpec(
    key=PROVIDER_FEISHU,
    title="Feishu",
    schema_builder=_build_schema,
    validate_config=async_validate_config,
    setup_provider=async_setup_provider,
)


class FeishuCardCallbackView(HomeAssistantView):
    requires_auth = False
    url = "/api/cn_im_hub/feishu/card_callback"
    name = "api:cn_im_hub:feishu:card_callback"

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def post(self, request: web.Request) -> web.Response:
        try:
            raw = await request.text()
            _LOGGER.info("Feishu card callback raw body: %s", raw[:2000])

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                _LOGGER.warning("Feishu card callback: invalid JSON body")
                return web.json_response({})

            if data.get("type") == "url_verification":
                challenge = data.get("challenge", "")
                _LOGGER.info("Feishu URL verification: challenge=%s", challenge)
                return web.json_response({"challenge": challenge})

            event = data.get("event", {})
            action = event.get("action", {})
            operator = event.get("operator", {})
            action_value = action.get("value", {})
            if isinstance(action_value, str):
                try:
                    action_value = json.loads(action_value)
                except (json.JSONDecodeError, TypeError):
                    action_value = {}

            event_data = {
                "action": action,
                "operator": operator,
                "token": data.get("token", ""),
                "raw_data": data,
            }

            self._hass.bus.async_fire(f"{DOMAIN}_feishu_card_action", event_data)
            _LOGGER.info(
                "Feishu card action fired: value=%s, operator=%s",
                json.dumps(action_value, ensure_ascii=False)[:300],
                json.dumps(operator, ensure_ascii=False)[:200],
            )

            toast_content = "OK"
            if isinstance(action_value, dict):
                toast_tmpl = action_value.get("toast")
                if toast_tmpl:
                    try:
                        toast_content = toast_tmpl.format(**action_value)
                    except (KeyError, IndexError, ValueError):
                        toast_content = toast_tmpl

            return web.json_response({
                "toast": {"type": "info", "content": toast_content}
            })

        except Exception:
            _LOGGER.exception("Feishu card callback error")
            return web.json_response({"error": "internal error"}, status=400)

    async def get(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "message": "Feishu card callback endpoint is active"})