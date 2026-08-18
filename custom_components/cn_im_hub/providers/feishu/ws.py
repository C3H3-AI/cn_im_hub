from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from homeassistant.core import HomeAssistant

from ...const import DOMAIN
from .api import import_lark

_LOGGER = logging.getLogger(__name__)

_EVENT_MESSAGE_READ = "im.message.message_read_v1"
_EVENT_MESSAGE_RECALL = "im.message.recalled_v1"
_EVENT_CARD_ACTION = "card.action.trigger"
_EVENT_MESSAGE_RECEIVE = "im.message.receive_v1"
_EVENT_BOT_ADDED = "im.chat.member.bot_added_v1"
_EVENT_BOT_DELETED = "im.chat.member.bot_deleted_v1"
_EVENT_USER_ADDED = "im.chat.member.user_added_v1"
_EVENT_USER_DELETED = "im.chat.member.user_deleted_v1"
_EVENT_CHAT_UPDATED = "im.chat.updated_v1"
_EVENT_CHAT_DISBANDED = "im.chat.disbanded_v1"


class FeishuWsClient:
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
        self._worker_thread: threading.Thread | None = None
        self._stop_flag = False
        self._seen_message_ids: OrderedDict[str, None] = OrderedDict()
        self._seen_limit = 512
        self._status = "disconnected"

    @property
    def status(self) -> str:
        return self._status

    async def async_start(self) -> None:
        if self._runner_task is not None:
            return
        self._stop_flag = False
        self._runner_task = self._hass.async_create_background_task(
            self._async_run_forever(),
            "cn_im_hub_feishu_ws_runner",
        )

    async def async_stop(self) -> None:
        self._stop_flag = True
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
        self._status = "connecting"
        self._worker_thread = threading.Thread(
            target=self._run_in_thread,
            daemon=True,
            name="feishu_ws_worker",
        )
        self._worker_thread.start()

    def _run_in_thread(self) -> None:
        import time
        max_retries = 8
        retry_count = 0

        while retry_count < max_retries and not self._stop_flag:
            try:
                self._start_sync_isolated()
                retry_count = 0
            except Exception as err:
                if self._stop_flag:
                    return
                retry_count += 1
                _LOGGER.warning("Feishu websocket error (attempt %d/%d): %s", retry_count, max_retries, err)
                if retry_count >= max_retries:
                    _LOGGER.error("Feishu connection failed after %d attempts, stopping", max_retries)
                    self._status = "error"
                    return
            if self._stop_flag:
                return
            self._status = "disconnected"
            time.sleep(5)
            self._status = "connecting"

    def _start_sync_isolated(self) -> None:
        import importlib
        import sys

        for mod_name in list(sys.modules.keys()):
            if mod_name.startswith("lark_oapi.ws"):
                del sys.modules[mod_name]

        lark, _ = import_lark()
        import lark_oapi.ws.client as lark_ws_client_mod

        worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(worker_loop)
        lark_ws_client_mod.loop = worker_loop

        builder = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_customized_event(_EVENT_MESSAGE_RECEIVE, self._on_custom_message_sync)
            .register_p2_customized_event(_EVENT_MESSAGE_READ, self._on_message_read_sync)
            .register_p2_customized_event(_EVENT_MESSAGE_RECALL, self._on_message_recall_sync)
            .register_p2_customized_event(_EVENT_CARD_ACTION, self._on_card_action_sync)
            .register_p2_customized_event(_EVENT_BOT_ADDED, self._on_chat_event_sync)
            .register_p2_customized_event(_EVENT_BOT_DELETED, self._on_chat_event_sync)
            .register_p2_customized_event(_EVENT_USER_ADDED, self._on_chat_event_sync)
            .register_p2_customized_event(_EVENT_USER_DELETED, self._on_chat_event_sync)
            .register_p2_customized_event(_EVENT_CHAT_UPDATED, self._on_chat_event_sync)
            .register_p2_customized_event(_EVENT_CHAT_DISBANDED, self._on_chat_event_sync)
        )
        event_handler = builder.build()

        self._client = lark.ws.Client(
            self._app_id,
            self._app_secret,
            event_handler=event_handler,
            log_level=lark.LogLevel.INFO,
        )

        self._status = "connected"

        try:
            self._client.start()
        finally:
            try:
                worker_loop.close()
            except Exception:
                pass

    def _stop_sync(self) -> None:
        client = self._client
        self._client = None
        stop = getattr(client, "stop", None) if client is not None else None
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

        sender_id = sender.get("sender_id") if isinstance(sender, dict) else None
        raw_content = str(message.get("content") or "")
        msg_type = str(message.get("msg_type") or "")

        future = asyncio.run_coroutine_threadsafe(
            self._message_handler(
                {
                    "message_id": message_id,
                    "text": _extract_text(raw_content, msg_type),
                    "chat_id": str(message.get("chat_id") or ""),
                    "user_id": _extract_user_id(sender_id),
                    "msg_type": msg_type,
                    "raw_content": raw_content,
                    "parent_id": str(message.get("parent_id") or ""),
                    "root_id": str(message.get("root_id") or ""),
                    "chat_type": str(message.get("chat_type") or ""),
                }
            ),
            self._hass.loop,
        )
        future.add_done_callback(_log_future_exception)

    def _on_message_read_sync(self, data: object) -> None:
        """Fire bus event when a message is read by the user."""
        event = getattr(data, "event", None)
        if not isinstance(event, dict):
            return
        self._hass.loop.call_soon_threadsafe(
            self._hass.bus.async_fire,
            f"{DOMAIN}_feishu_message_read",
            {"raw_data": event},
        )

    def _on_message_recall_sync(self, data: object) -> None:
        """Fire bus event when a message is recalled by the user."""
        event = getattr(data, "event", None)
        if not isinstance(event, dict):
            return
        message = event.get("message") or {}
        message_id = str(message.get("message_id") or "")
        chat_id = str(message.get("chat_id") or "")
        if not message_id:
            return
        self._hass.loop.call_soon_threadsafe(
            self._hass.bus.async_fire,
            f"{DOMAIN}_feishu_message_recalled",
            {
                "message_id": message_id,
                "chat_id": chat_id,
                "raw_data": event,
            },
        )

    def _on_card_action_sync(self, data: object) -> None:
        event = getattr(data, "event", None)
        if not isinstance(event, dict):
            return
        action = event.get("action") or {}
        operator = event.get("operator") or {}
        value = _decode_action_value(action.get("value", {}))
        # Non-AI card actions: fire bus event for automation processing
        if not (isinstance(value, dict) and value.get("from_ai")):
            self._hass.loop.call_soon_threadsafe(
                self._hass.bus.async_fire,
                f"{DOMAIN}_feishu_card_action",
                {"action": action, "operator": operator, "raw_data": event, "from_ai": False},
            )
            return
        user_text = ""
        if isinstance(value, dict):
            user_text = value.get("action", "")
        elif isinstance(value, str):
            user_text = value
        if not user_text:
            return
        operator_id = operator.get("open_id") or ""
        open_chat_id = event.get("context", {}).get("open_chat_id", "") if isinstance(event.get("context"), dict) else ""
        future = asyncio.run_coroutine_threadsafe(
            self._message_handler(
                {
                    "message_id": "",
                    "text": user_text,
                    "chat_id": open_chat_id,
                    "user_id": operator_id,
                    "msg_type": "card_action",
                    "raw_content": "",
                    "parent_id": "",
                    "root_id": "",
                    "chat_type": "",
                }
            ),
            self._hass.loop,
        )
        future.add_done_callback(_log_future_exception)

    def _on_chat_event_sync(self, data: object) -> None:
        """Fire bus event for chat lifecycle events (bot_added, user_added, etc.)."""
        event = getattr(data, "event", None)
        if not isinstance(event, dict):
            return
        self._hass.loop.call_soon_threadsafe(
            self._hass.bus.async_fire,
            f"{DOMAIN}_feishu_chat_event",
            {"raw_data": event},
        )


def _extract_user_id(sender_id: object) -> str:
    return str(
        sender_id.get("open_id") or sender_id.get("user_id") or sender_id.get("union_id") or ""
    ) if isinstance(sender_id, dict) else ""


def _extract_text(content: str, msg_type: str = "") -> str:
    """Extract human-readable text from message content based on msg_type.

    Supports both 'text' and 'post' (rich text) message types.
    Falls back to raw content if parsing fails.
    """
    if not content:
        return ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content.strip()
    if not isinstance(payload, dict):
        return content.strip()

    if msg_type == "text":
        return str(payload.get("text", "")).strip()

    if msg_type == "post":
        return _extract_post_text(payload)

    # Fallback for unknown types: try 'text' field, then entire content
    text = str(payload.get("text", "")).strip()
    if text:
        return text
    return content.strip()


def _extract_post_text(payload: dict) -> str:
    """Extract plain text from Feishu post (rich text) content."""
    # Try zh_cn first, then en_us, then any language
    for lang_key in ("zh_cn", "en_us", "ja_jp"):
        lang_content = payload.get(lang_key)
        if isinstance(lang_content, dict):
            content_blocks = lang_content.get("content")
            if isinstance(content_blocks, list):
                parts = _extract_post_blocks(content_blocks)
                if parts:
                    return " ".join(parts)
    # Try top-level content field
    content_blocks = payload.get("content")
    if isinstance(content_blocks, list):
        parts = _extract_post_blocks(content_blocks)
        if parts:
            return " ".join(parts)
    # Try title
    title = str(payload.get("title", "")).strip()
    if title:
        return title
    return ""


def _extract_post_blocks(blocks: list) -> list[str]:
    """Extract text strings from post content blocks."""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, list):
            for element in block:
                if isinstance(element, dict):
                    tag = element.get("tag", "")
                    if tag == "text":
                        text = str(element.get("text", "")).strip()
                        if text:
                            parts.append(text)
                    elif tag == "a":
                        text = str(element.get("text", "")).strip()
                        if text:
                            parts.append(text)
                    elif tag == "at":
                        text = str(element.get("user_name", "")).strip()
                        if text:
                            parts.append(f"@{text}")
    return parts


def _decode_action_value(value: object) -> object:
    """飞书卡片 action.value 可能是 dict 或 JSON 字符串，统一解码。

    字符串尝试 JSON 解析，失败或非字符串则原样返回。确保 from_ai 等字段
    在 value 为字符串化 JSON 时仍能正确读取。
    """
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return {}


def _log_future_exception(future: asyncio.Future) -> None:
    try:
        future.result()
    except Exception:
        _LOGGER.exception("Failed to process Feishu message")