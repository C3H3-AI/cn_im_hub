"""XiaoYi provider — full A2A protocol implementation.

Supports all official XiaoYi OpenClaw A2A protocol methods:
- message/stream (text, file, data parts)
- tasks/cancel
- clearContext
- initialize / notifications/initialized
- authorize / deauthorize
- PUSH notification (proactive push)

Response features:
- artifact-update with text, reasoningText, data (cards, chips, reference, commands)
- status-update (working, completed, failed, input-required)
- Image sending via send_image
- Card sending via send_card
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import logging
import ssl
import time
from base64 import b64encode
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ..command import execute_command, parse_command
from ..const import (
    CONF_XIAOYI_AGENT_ID,
    CONF_XIAOYI_AK,
    CONF_XIAOYI_SK,
    CONF_XIAOYI_WS_URL_1,
    CONF_XIAOYI_WS_URL_2,
    PROVIDER_XIAOYI,
    XIAOYI_DEFAULT_WS_URL_1,
    XIAOYI_DEFAULT_WS_URL_2,
)
from ..known_targets import async_get_tracker
from ..models import ProviderRuntime
from .base import ProviderSpec

_LOGGER = logging.getLogger(__name__)

_SERVER_IDS = ("server1", "server2")
_STABLE_CONNECTION_THRESHOLD = 30.0
_MAX_RECONNECT_ATTEMPTS = 0
_WATCHDOG_INTERVAL = 20.0
_WATCHDOG_TIMEOUT = 0.0

_PUSH_WEBHOOK_URL = "https://hag.cloud.huawei.com/open-ability-agent/v1/agent-webhook"


@dataclass(slots=True)
class _ServerState:
    connected: bool = False
    ready: bool = False
    last_heartbeat: float = 0.0
    reconnect_attempts: int = 0


@dataclass
class _SessionContext:
    session_id: str
    server_id: str
    agent_login_session_id: str = ""
    task_ids: set[str] = field(default_factory=set)


class XiaoYiClient:
    """Dual-websocket XiaoYi client with full A2A protocol support."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        ak: str,
        sk: str,
        xiaoyi_agent_id: str,
        conversation_agent_id: str,
        ws_url_1: str,
        ws_url_2: str,
    ) -> None:
        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._ak = ak
        self._sk = sk
        self._xiaoyi_agent_id = xiaoyi_agent_id
        self._conversation_agent_id = conversation_agent_id
        self._urls = {"server1": ws_url_1, "server2": ws_url_2}
        self._states = {server_id: _ServerState() for server_id in _SERVER_IDS}
        self._ws: dict[str, aiohttp.ClientWebSocketResponse | None] = {
            server_id: None for server_id in _SERVER_IDS
        }
        self._listen_tasks: dict[str, asyncio.Task[None] | None] = {
            server_id: None for server_id in _SERVER_IDS
        }
        self._reconnect_tasks: dict[str, asyncio.Task[None] | None] = {
            server_id: None for server_id in _SERVER_IDS
        }
        self._stable_tasks: dict[str, asyncio.Task[None] | None] = {
            server_id: None for server_id in _SERVER_IDS
        }
        self._app_heartbeat_task: asyncio.Task[None] | None = None
        self._watchdog_task: asyncio.Task[None] | None = None
        self._active_prompts: dict[str, asyncio.Task[str]] = {}
        self._session_servers: dict[str, str] = {}
        self._session_contexts: dict[str, _SessionContext] = {}
        self._stopping = False
        self._tracker = None

    @property
    def status(self) -> str:
        if any(state.ready for state in self._states.values()):
            return "connected"
        if any(state.connected for state in self._states.values()):
            return "connecting"
        return "disconnected"

    async def start(self) -> None:
        self._stopping = False
        results = await asyncio.gather(
            self._connect_to_server("server1"),
            self._connect_to_server("server2"),
            return_exceptions=True,
        )
        if all(isinstance(result, Exception) for result in results):
            raise RuntimeError("Failed to connect to both XiaoYi servers")
        if self._app_heartbeat_task is None:
            self._app_heartbeat_task = asyncio.create_task(
                self._app_heartbeat_loop()
            )
        if self._watchdog_task is None:
            self._watchdog_task = asyncio.create_task(self._watchdog_loop())

    async def stop(self) -> None:
        self._stopping = True
        for task in self._active_prompts.values():
            task.cancel()
        for task_map in (
            self._listen_tasks,
            self._reconnect_tasks,
            self._stable_tasks,
        ):
            for task in task_map.values():
                if task is not None:
                    task.cancel()
        if self._app_heartbeat_task is not None:
            self._app_heartbeat_task.cancel()
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()

        pending = [*self._active_prompts.values()]
        for task_map in (
            self._listen_tasks,
            self._reconnect_tasks,
            self._stable_tasks,
        ):
            pending.extend(task for task in task_map.values() if task is not None)
        if self._app_heartbeat_task is not None:
            pending.append(self._app_heartbeat_task)
        if self._watchdog_task is not None:
            pending.append(self._watchdog_task)
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError):
                await task

        for server_id in _SERVER_IDS:
            await self._close_server(server_id)
            self._states[server_id] = _ServerState()
            self._listen_tasks[server_id] = None
            self._reconnect_tasks[server_id] = None
            self._stable_tasks[server_id] = None

        self._active_prompts.clear()
        self._session_servers.clear()
        self._session_contexts.clear()
        self._app_heartbeat_task = None
        self._watchdog_task = None

    def _ensure_session_route(self, session_id: str) -> str:
        server_id = self._session_servers.get(session_id)
        if server_id:
            return server_id
        for sid in _SERVER_IDS:
            if self._states[sid].ready and self._ws.get(sid) is not None and not self._ws[sid].closed:
                self._session_servers[session_id] = sid
                if session_id not in self._session_contexts:
                    self._session_contexts[session_id] = _SessionContext(
                        session_id=session_id, server_id=sid
                    )
                _LOGGER.info("XiaoYi auto-routed session %s to %s", session_id, sid)
                return sid
        raise ValueError("XiaoYi no active WebSocket connection available")

    async def send_text(self, target: str, message: str, target_type: str) -> None:
        if target_type != "session_id":
            raise ValueError("XiaoYi send_message only supports target_type=session_id")
        session_id = target.strip()
        if not session_id:
            raise ValueError("XiaoYi session_id is required")
        self._ensure_session_route(session_id)
        task_id = str(uuid4())
        await self._send_text_chunk(task_id, session_id, message)
        await self._send_final(task_id, session_id)

    async def send_image(self, target: str, image_data: bytes, target_type: str) -> None:
        if target_type != "session_id":
            raise ValueError("XiaoYi send_image only supports target_type=session_id")
        session_id = target.strip()
        if not session_id:
            raise ValueError("XiaoYi session_id is required")
        self._ensure_session_route(session_id)
        task_id = str(uuid4())
        import base64

        b64_data = base64.b64encode(image_data).decode("ascii")
        await self._send_jsonrpc_result(
            request_id=str(uuid4()),
            session_id=session_id,
            task_id=task_id,
            result={
                "taskId": task_id,
                "kind": "artifact-update",
                "append": True,
                "lastChunk": False,
                "final": False,
                "artifact": {
                    "artifactId": f"artifact_{uuid4().hex}",
                    "parts": [
                        {
                            "kind": "file",
                            "file": {
                                "name": "image.png",
                                "mimeType": "image/png",
                                "bytes": b64_data,
                            },
                        }
                    ],
                },
            },
        )
        await self._send_final(task_id, session_id)

    async def send_card(
        self, target: str, card_data: dict[str, Any], target_type: str
    ) -> None:
        if target_type != "session_id":
            raise ValueError("XiaoYi send_card only supports target_type=session_id")
        session_id = target.strip()
        if not session_id:
            raise ValueError("XiaoYi session_id is required")
        self._ensure_session_route(session_id)
        task_id = str(uuid4())
        await self._send_artifact_with_data(
            task_id, session_id, cards_info=[card_data]
        )
        await self._send_final(task_id, session_id)

    async def send_push(
        self,
        session_id: str,
        push_text: str,
        *,
        agent_login_session_id: str = "",
        artifacts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        headers = _build_auth_headers(self._ak, self._sk, self._xiaoyi_agent_id)
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "application/json"
        headers["x-hag-trace-id"] = str(uuid4())

        request_id = str(uuid4())
        result_id = str(uuid4())
        body: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "id": result_id,
                "pushId": str(uuid4()),
                "pushText": push_text,
                "kind": "task",
                "artifacts": artifacts or [],
                "status": {"state": "completed"},
            },
        }
        if agent_login_session_id:
            body["result"]["agentLoginSessionId"] = agent_login_session_id

        try:
            async with self._session.post(
                _PUSH_WEBHOOK_URL, headers=headers, json=body, timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                return await resp.json(content_type=None)
        except Exception as err:
            _LOGGER.error("XiaoYi PUSH notification failed: %s", err)
            return {"error": {"code": -1, "message": str(err)}}

    async def _connect_to_server(self, server_id: str) -> None:
        url = self._urls[server_id]
        headers = _build_auth_headers(self._ak, self._sk, self._xiaoyi_agent_id)
        ssl_context = _build_ws_ssl_context(url)
        if ssl_context is not True:
            _LOGGER.info(
                "XiaoYi %s uses insecure TLS fallback for IP endpoint", server_id
            )

        _LOGGER.info("Connecting XiaoYi %s: %s", server_id, url)
        try:
            ws = await self._session.ws_connect(
                url,
                headers=headers,
                heartbeat=30,
                autoping=True,
                receive_timeout=90,
                timeout=30,
                ssl=ssl_context,
            )
        except Exception:
            self._states[server_id].connected = False
            self._states[server_id].ready = False
            raise

        self._ws[server_id] = ws
        state = self._states[server_id]
        state.connected = True
        state.ready = True
        state.last_heartbeat = time.time()

        await ws.send_json(
            {"msgType": "clawd_bot_init", "agentId": self._xiaoyi_agent_id}
        )
        self._schedule_stable_connection_check(server_id)
        self._listen_tasks[server_id] = asyncio.create_task(
            self._listen_server(server_id, ws)
        )
        _LOGGER.info("XiaoYi %s connected", server_id)

    async def _listen_server(
        self, server_id: str, ws: aiohttp.ClientWebSocketResponse
    ) -> None:
        try:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    self._states[server_id].last_heartbeat = time.time()
                    await self._handle_message(server_id, json.loads(msg.data))
                elif msg.type in (aiohttp.WSMsgType.PING, aiohttp.WSMsgType.PONG):
                    self._states[server_id].last_heartbeat = time.time()
                elif msg.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.warning(
                "XiaoYi websocket loop error (%s): %s", server_id, err
            )
        finally:
            await self._handle_disconnect(server_id)

    async def _handle_disconnect(self, server_id: str) -> None:
        await self._close_server(server_id)
        state = self._states[server_id]
        state.connected = False
        state.ready = False
        stable_task = self._stable_tasks[server_id]
        if stable_task is not None:
            stable_task.cancel()
            self._stable_tasks[server_id] = None
        if not self._stopping:
            self._schedule_reconnect(server_id)

    async def _close_server(self, server_id: str) -> None:
        ws = self._ws.get(server_id)
        if ws is not None and not ws.closed:
            with contextlib.suppress(Exception):
                await ws.close()
        self._ws[server_id] = None

    def _schedule_reconnect(self, server_id: str) -> None:
        current = self._reconnect_tasks.get(server_id)
        if current is not None and not current.done():
            return
        self._reconnect_tasks[server_id] = asyncio.create_task(
            self._reconnect_server(server_id)
        )

    async def _reconnect_server(self, server_id: str) -> None:
        state = self._states[server_id]
        if _MAX_RECONNECT_ATTEMPTS and state.reconnect_attempts >= _MAX_RECONNECT_ATTEMPTS:
            _LOGGER.error("XiaoYi %s reached max reconnect attempts", server_id)
            return
        delay = min(2 * (2**state.reconnect_attempts), 60)
        state.reconnect_attempts += 1
        await asyncio.sleep(delay)
        if self._stopping:
            return
        try:
            await self._connect_to_server(server_id)
        except Exception as err:
            _LOGGER.warning("XiaoYi reconnect failed (%s): %s", server_id, err)
            self._schedule_reconnect(server_id)

    def _schedule_stable_connection_check(self, server_id: str) -> None:
        current = self._stable_tasks.get(server_id)
        if current is not None and not current.done():
            current.cancel()
        self._stable_tasks[server_id] = asyncio.create_task(
            self._stable_connection_check(server_id)
        )

    async def _stable_connection_check(self, server_id: str) -> None:
        try:
            await asyncio.sleep(_STABLE_CONNECTION_THRESHOLD)
            if self._states[server_id].connected:
                self._states[server_id].reconnect_attempts = 0
        except asyncio.CancelledError:
            raise

    async def _app_heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(20)
                payload = {"msgType": "heartbeat", "agentId": self._xiaoyi_agent_id}
                for server_id in _SERVER_IDS:
                    ws = self._ws.get(server_id)
                    if ws is not None and not ws.closed:
                        try:
                            await ws.send_json(payload)
                        except Exception as err:
                            _LOGGER.debug(
                                "XiaoYi heartbeat send failed (%s): %s",
                                server_id,
                                err,
                            )
        except asyncio.CancelledError:
            raise

    async def _watchdog_loop(self) -> None:
        if _WATCHDOG_TIMEOUT <= 0:
            return
        try:
            while True:
                await asyncio.sleep(_WATCHDOG_INTERVAL)
                now = time.time()
                for server_id in _SERVER_IDS:
                    ws = self._ws.get(server_id)
                    state = self._states[server_id]
                    if ws is None or ws.closed or not state.connected:
                        continue
                    if now - state.last_heartbeat > _WATCHDOG_TIMEOUT:
                        _LOGGER.warning(
                            "XiaoYi %s heartbeat timeout, reconnecting", server_id
                        )
                        with contextlib.suppress(Exception):
                            await ws.close()
        except asyncio.CancelledError:
            raise

    async def _handle_message(
        self, server_id: str, message: dict[str, Any]
    ) -> None:
        agent_id = str(message.get("agentId") or "")
        if agent_id and agent_id != self._xiaoyi_agent_id:
            _LOGGER.debug("Ignore XiaoYi message for agent_id=%s", agent_id)
            return

        session_id = _extract_session_id(message)
        if session_id:
            self._session_servers[session_id] = server_id
            if session_id not in self._session_contexts:
                self._session_contexts[session_id] = _SessionContext(
                    session_id=session_id, server_id=server_id
                )

        method = str(message.get("method") or "")
        action = str(message.get("action") or "")

        if method == "initialize":
            await self._handle_initialize(message, session_id)
            return
        if method == "notifications/initialized":
            _LOGGER.info("XiaoYi client initialized notification received")
            return
        if method == "authorize":
            await self._handle_authorize(message, session_id)
            return
        if method == "deauthorize":
            await self._handle_deauthorize(message, session_id)
            return
        if method == "clearContext":
            if session_id:
                await self._send_clear_context_response(
                    str(message.get("id") or uuid4()), session_id
                )
                self._session_contexts.pop(session_id, None)
            return
        if action == "clear":
            if session_id:
                await self._send_clear_context_response(
                    str(message.get("id") or uuid4()), session_id
                )
                self._session_contexts.pop(session_id, None)
            return
        if method == "tasks/cancel" or action == "tasks/cancel":
            if session_id:
                await self._handle_cancel(message, session_id)
            return
        if method != "message/stream":
            _LOGGER.debug("XiaoYi unhandled method: %s", method)
            return

        task_id = str(message.get("id") or "")
        if not task_id or not session_id:
            return

        inbound = _extract_inbound_parts(message)
        text = inbound.get("text", "")
        files = inbound.get("files", [])
        data_parts = inbound.get("data", [])

        if session_id in self._session_contexts:
            ctx = self._session_contexts[session_id]
            ctx.task_ids.add(task_id)
            login_id = _extract_agent_login_session_id(message)
            if login_id:
                ctx.agent_login_session_id = login_id

        if self._tracker is not None:
            await self._tracker.async_record(
                provider=PROVIDER_XIAOYI,
                target=session_id,
                target_type="session_id",
                display_name=session_id,
            )

        task = asyncio.create_task(
            self._process_prompt(task_id, session_id, text, files, data_parts)
        )
        self._active_prompts[task_id] = task
        task.add_done_callback(lambda _: self._active_prompts.pop(task_id, None))

    async def _handle_initialize(
        self, message: dict[str, Any], session_id: str
    ) -> None:
        request_id = str(message.get("id") or uuid4())
        agent_session_id = str(uuid4())
        _LOGGER.info("XiaoYi initialize request, session_id=%s", session_id)
        await self._send_jsonrpc_result(
            request_id=request_id,
            session_id=session_id or str(uuid4()),
            task_id=request_id,
            result={
                "version": "1.0",
                "agentSessionId": agent_session_id,
                "agentSessionTtl": 604800,
            },
        )

    async def _handle_authorize(
        self, message: dict[str, Any], session_id: str
    ) -> None:
        request_id = str(message.get("id") or uuid4())
        login_session_id = str(uuid4())
        _LOGGER.info("XiaoYi authorize request, session_id=%s", session_id)

        if session_id and session_id in self._session_contexts:
            self._session_contexts[session_id].agent_login_session_id = (
                login_session_id
            )

        await self._send_jsonrpc_result(
            request_id=request_id,
            session_id=session_id or str(uuid4()),
            task_id=request_id,
            result={
                "version": "1.0",
                "agentLoginSessionId": login_session_id,
            },
        )

    async def _handle_deauthorize(
        self, message: dict[str, Any], session_id: str
    ) -> None:
        request_id = str(message.get("id") or uuid4())
        _LOGGER.info("XiaoYi deauthorize request, session_id=%s", session_id)

        if session_id and session_id in self._session_contexts:
            self._session_contexts[session_id].agent_login_session_id = ""

        await self._send_jsonrpc_result(
            request_id=request_id,
            session_id=session_id or str(uuid4()),
            task_id=request_id,
            result={"version": "1.0"},
        )

    async def _process_prompt(
        self,
        task_id: str,
        session_id: str,
        text: str,
        files: list[dict[str, Any]],
        data_parts: list[dict[str, Any]],
    ) -> str:
        if not text and not files and not data_parts:
            await self._send_final(task_id, session_id)
            return ""

        try:
            await self._send_status_update(
                task_id, session_id, "working", "思考中"
            )

            enriched_text = text
            if files:
                file_desc_parts = []
                for f in files:
                    name = f.get("name", "unknown")
                    mime = f.get("mimeType", "unknown")
                    file_desc_parts.append(f"[文件: {name}, 类型: {mime}]")
                enriched_text = f"{' '.join(file_desc_parts)}\n{text}" if text else " ".join(file_desc_parts)

            if data_parts:
                data_desc_parts = []
                for d in data_parts:
                    data_desc_parts.append(f"[结构化数据: {json.dumps(d, ensure_ascii=False)[:200]}]")
                enriched_text = f"{enriched_text}\n{' '.join(data_desc_parts)}" if enriched_text else " ".join(data_desc_parts)

            command = parse_command(enriched_text)
            reply = ""
            if command is not None:
                reply = await execute_command(
                    self._hass,
                    command,
                    conversation_id=f"xiaoyi:{session_id}",
                    agent_id=self._conversation_agent_id or None,
                )
            if reply:
                await self._send_text_chunk(task_id, session_id, reply)
            await self._send_final(task_id, session_id)
            return reply
        except asyncio.CancelledError:
            await self._send_cancelled(task_id, session_id)
            raise
        except Exception as err:
            _LOGGER.exception("XiaoYi prompt handling failed: %s", err)
            await self._send_error(task_id, session_id, err)
            return ""

    async def _handle_cancel(
        self, message: dict[str, Any], session_id: str
    ) -> None:
        cancel_task_id = str(message.get("taskId") or message.get("id") or "")
        if cancel_task_id:
            task = self._active_prompts.get(cancel_task_id)
            if task is not None:
                task.cancel()
        await self._send_tasks_cancel_response(
            str(message.get("id") or uuid4()), session_id
        )

    async def _send_status_update(
        self,
        task_id: str,
        session_id: str,
        state: str,
        description: str = "",
    ) -> None:
        await self._send_jsonrpc_result(
            request_id=str(uuid4()),
            session_id=session_id,
            task_id=task_id,
            result={
                "taskId": task_id,
                "kind": "status-update",
                "final": False,
                "status": {
                    "message": {
                        "role": "agent",
                        "parts": [{"kind": "text", "text": description}],
                    },
                    "state": state,
                },
            },
        )

    async def _send_tasks_cancel_response(
        self, request_id: str, session_id: str
    ) -> None:
        await self._send_jsonrpc_result(
            request_id=request_id,
            session_id=session_id,
            task_id=request_id,
            result={"id": request_id, "status": {"state": "canceled"}},
        )

    async def _send_clear_context_response(
        self, request_id: str, session_id: str
    ) -> None:
        await self._send_jsonrpc_result(
            request_id=request_id,
            session_id=session_id,
            task_id=request_id,
            result={"status": {"state": "cleared"}},
        )

    async def _send_text_chunk(
        self, task_id: str, session_id: str, text: str
    ) -> None:
        await self._send_jsonrpc_result(
            request_id=str(uuid4()),
            session_id=session_id,
            task_id=task_id,
            result={
                "taskId": task_id,
                "kind": "artifact-update",
                "append": True,
                "lastChunk": False,
                "final": False,
                "artifact": {
                    "artifactId": f"artifact_{uuid4().hex}",
                    "parts": [{"kind": "text", "text": text}],
                },
            },
        )

    async def _send_reasoning_chunk(
        self, task_id: str, session_id: str, reasoning: str
    ) -> None:
        await self._send_jsonrpc_result(
            request_id=str(uuid4()),
            session_id=session_id,
            task_id=task_id,
            result={
                "taskId": task_id,
                "kind": "artifact-update",
                "append": True,
                "lastChunk": False,
                "final": False,
                "artifact": {
                    "artifactId": f"artifact_{uuid4().hex}",
                    "parts": [{"kind": "reasoningText", "reasoningText": reasoning}],
                },
            },
        )

    async def _send_artifact_with_data(
        self,
        task_id: str,
        session_id: str,
        *,
        cards_info: list[dict[str, Any]] | None = None,
        chips_info: dict[str, Any] | None = None,
        reference: dict[str, Any] | None = None,
        commands: list[dict[str, Any]] | None = None,
        text: str = "",
    ) -> None:
        data_obj: dict[str, Any] = {}
        if cards_info:
            data_obj["cardsInfo"] = cards_info
        if chips_info:
            data_obj["chipsInfo"] = chips_info
        if reference:
            data_obj["reference"] = reference
        if commands:
            data_obj["commands"] = commands

        parts: list[dict[str, Any]] = []
        if text:
            parts.append({"kind": "text", "text": text})
        if data_obj:
            parts.append({"kind": "data", "data": data_obj})

        if not parts:
            return

        await self._send_jsonrpc_result(
            request_id=str(uuid4()),
            session_id=session_id,
            task_id=task_id,
            result={
                "taskId": task_id,
                "kind": "artifact-update",
                "append": True,
                "lastChunk": False,
                "final": False,
                "artifact": {
                    "artifactId": f"artifact_{uuid4().hex}",
                    "parts": parts,
                },
            },
        )

    async def _send_final(self, task_id: str, session_id: str) -> None:
        await self._send_jsonrpc_result(
            request_id=str(uuid4()),
            session_id=session_id,
            task_id=task_id,
            result={
                "taskId": task_id,
                "kind": "artifact-update",
                "append": True,
                "lastChunk": True,
                "final": True,
                "artifact": {
                    "artifactId": f"artifact_{uuid4().hex}",
                    "parts": [{"kind": "text", "text": ""}],
                },
            },
        )

    async def _send_cancelled(self, task_id: str, session_id: str) -> None:
        await self._send_jsonrpc_result(
            request_id=str(uuid4()),
            session_id=session_id,
            task_id=task_id,
            result={
                "taskId": task_id,
                "kind": "status-update",
                "final": True,
                "status": {
                    "message": {
                        "role": "agent",
                        "parts": [{"kind": "text", "text": ""}],
                    },
                    "state": "canceled",
                },
            },
        )

    async def _send_error(
        self, task_id: str, session_id: str, err: Exception
    ) -> None:
        await self._send_jsonrpc_error(
            request_id=str(uuid4()),
            session_id=session_id,
            task_id=task_id,
            code="internal_error",
            message=f"{type(err).__name__}: {err}",
        )

    async def _send_jsonrpc_result(
        self,
        *,
        request_id: str,
        session_id: str,
        task_id: str,
        result: dict[str, Any],
    ) -> None:
        await self._send_outbound(
            session_id=session_id,
            task_id=task_id,
            detail={"jsonrpc": "2.0", "id": request_id, "result": result},
        )

    async def _send_jsonrpc_error(
        self,
        *,
        request_id: str,
        session_id: str,
        task_id: str,
        code: str,
        message: str,
    ) -> None:
        await self._send_outbound(
            session_id=session_id,
            task_id=task_id,
            detail={
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": code, "message": message},
            },
        )

    async def _send_outbound(
        self, *, session_id: str, task_id: str, detail: dict[str, Any]
    ) -> None:
        server_id = self._session_servers.get(session_id)
        if not server_id:
            raise RuntimeError(f"XiaoYi session route missing: {session_id}")
        ws = self._ws.get(server_id)
        if ws is None or ws.closed:
            raise RuntimeError(f"XiaoYi websocket unavailable: {server_id}")
        await ws.send_json(
            {
                "msgType": "agent_response",
                "agentId": self._xiaoyi_agent_id,
                "sessionId": session_id,
                "taskId": task_id,
                "msgDetail": json.dumps(detail, ensure_ascii=True),
            }
        )


def _build_auth_headers(ak: str, sk: str, agent_id: str) -> dict[str, str]:
    timestamp = str(int(time.time() * 1000))
    digest = hmac.new(
        sk.encode("utf-8"), timestamp.encode("utf-8"), hashlib.sha256
    ).digest()
    return {
        "x-access-key": ak,
        "x-sign": b64encode(digest).decode("ascii"),
        "x-ts": timestamp,
        "x-agent-id": agent_id,
    }


def _build_ws_ssl_context(url: str) -> ssl.SSLContext | bool:
    parsed = urlparse(url)
    if parsed.scheme != "wss" or not _is_ip_host(parsed.hostname or ""):
        return True
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _is_ip_host(host: str) -> bool:
    if not host:
        return False
    parts = host.split(".")
    if len(parts) == 4 and all(part.isdigit() for part in parts):
        return all(0 <= int(part) <= 255 for part in parts)
    return ":" in host


def _extract_session_id(message: dict[str, Any]) -> str:
    method = str(message.get("method") or "")
    action = str(message.get("action") or "")
    if method == "message/stream":
        return str(
            (message.get("params") or {}).get("sessionId")
            or message.get("sessionId")
            or ""
        )
    if (
        method in {"tasks/cancel", "clearContext", "initialize", "authorize", "deauthorize"}
        or action in {"tasks/cancel", "clear"}
    ):
        return str(message.get("sessionId") or "")
    return ""


def _extract_agent_login_session_id(message: dict[str, Any]) -> str:
    params = message.get("params") or {}
    return str(params.get("agentLoginSessionId") or "")


def _extract_inbound_parts(message: dict[str, Any]) -> dict[str, Any]:
    parts = (
        ((message.get("params") or {}).get("message") or {}).get("parts") or []
    )
    text_chunks: list[str] = []
    files: list[dict[str, Any]] = []
    data_parts: list[dict[str, Any]] = []

    for part in parts:
        if not isinstance(part, dict):
            continue
        kind = part.get("kind", "")

        if kind == "text":
            value = str(part.get("text") or "").strip()
            if value:
                text_chunks.append(value)

        elif kind == "file":
            file_obj = part.get("file") or {}
            file_info: dict[str, Any] = {
                "name": str(file_obj.get("name") or ""),
                "mimeType": str(file_obj.get("mimeType") or ""),
            }
            if file_obj.get("bytes"):
                file_info["bytes"] = str(file_obj["bytes"])
            if file_obj.get("uri"):
                file_info["uri"] = str(file_obj["uri"])
            files.append(file_info)

        elif kind == "data":
            data_content = part.get("data") or {}
            data_parts.append(data_content)

    return {
        "text": "\n".join(text_chunks).strip(),
        "files": files,
        "data": data_parts,
    }


def _extract_inbound_text(message: dict[str, Any]) -> str:
    return _extract_inbound_parts(message).get("text", "")


async def async_validate_config(
    _: HomeAssistant, config: dict[str, Any]
) -> None:
    ak = str(config.get(CONF_XIAOYI_AK, "")).strip()
    sk = str(config.get(CONF_XIAOYI_SK, "")).strip()
    agent_id = str(config.get(CONF_XIAOYI_AGENT_ID, "")).strip()
    if not ak or not sk or not agent_id:
        raise ValueError("xiaoyi_ak, xiaoyi_sk, and xiaoyi_agent_id are required")


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, Any],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    client = XiaoYiClient(
        hass,
        ak=str(config.get(CONF_XIAOYI_AK, "")).strip(),
        sk=str(config.get(CONF_XIAOYI_SK, "")).strip(),
        xiaoyi_agent_id=str(config.get(CONF_XIAOYI_AGENT_ID, "")).strip(),
        conversation_agent_id=agent_id,
        ws_url_1=str(
            config.get(CONF_XIAOYI_WS_URL_1, XIAOYI_DEFAULT_WS_URL_1)
        ).strip()
        or XIAOYI_DEFAULT_WS_URL_1,
        ws_url_2=str(
            config.get(CONF_XIAOYI_WS_URL_2, XIAOYI_DEFAULT_WS_URL_2)
        ).strip()
        or XIAOYI_DEFAULT_WS_URL_2,
    )
    tracker = await async_get_tracker(hass, subentry_id)
    client._tracker = tracker
    await client.start()

    async def _send(target: str, message: str, target_type: str) -> None:
        await client.send_text(target, message, target_type)

    async def _send_image(target: str, image_data: bytes, target_type: str) -> None:
        await client.send_image(target, image_data, target_type)

    async def _send_card(
        target: str, card_data: dict[str, Any], target_type: str
    ) -> None:
        await client.send_card(target, card_data, target_type)

    return ProviderRuntime(
        key=PROVIDER_XIAOYI,
        title="XiaoYi",
        subentry_id=subentry_id,
        client=client,
        stop=client.stop,
        send_text=_send,
        send_image=_send_image,
        send_card=_send_card,
        status=lambda: client.status,
        known_targets=tracker.snapshot,
        selected_target=tracker.selected_target,
        select_target=tracker.async_select_target,
    )


def _build_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_XIAOYI_AK, default=current.get(CONF_XIAOYI_AK, "")
            ): str,
            vol.Required(
                CONF_XIAOYI_SK, default=current.get(CONF_XIAOYI_SK, "")
            ): str,
            vol.Required(
                CONF_XIAOYI_AGENT_ID,
                default=current.get(CONF_XIAOYI_AGENT_ID, ""),
            ): str,
        }
    )


PROVIDER_SPEC = ProviderSpec(
    key=PROVIDER_XIAOYI,
    title="XiaoYi",
    schema_builder=_build_schema,
    validate_config=async_validate_config,
    setup_provider=async_setup_provider,
)
