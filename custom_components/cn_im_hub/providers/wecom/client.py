"""WeCom provider implementation."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import uuid
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

EVENT_LIVE_PROGRESS = "ha_crack_live_progress"
_LIVE_PROGRESS_SEND_INTERVAL_SECONDS = 2.0

from ...core.command import execute_command, im_public_url_for_source, parse_command
from ...const import CONF_WECOM_BOT_ID, CONF_WECOM_SECRET, PROVIDER_WECOM, WECOM_WS_URL
from ...core.known_targets import async_get_tracker
from ...media.rich_media import (
    FileSegment,
    ImageSegment,
    TextSegment,
    VideoSegment,
    VoiceSegment,
    parse_reply_segments,
)
from ...models import ProviderRuntime
from ..base import ProviderSpec
from .prompt import build_wecom_prompt

_LOGGER = logging.getLogger(__name__)

WS_URL = WECOM_WS_URL
CMD_SUBSCRIBE = "aibot_subscribe"
CMD_HEARTBEAT = "ping"
CMD_SEND_MSG = "aibot_send_msg"
CMD_RESPOND_MSG = "aibot_respond_msg"
CMD_RESPOND_WELCOME = "aibot_respond_welcome_msg"
CMD_MSG_CALLBACK = "aibot_msg_callback"
CMD_EVENT_CALLBACK = "aibot_event_callback"
CMD_UPLOAD_MEDIA_INIT = "aibot_upload_media_init"
CMD_UPLOAD_MEDIA_CHUNK = "aibot_upload_media_chunk"
CMD_UPLOAD_MEDIA_FINISH = "aibot_upload_media_finish"
EVENT_ENTER_CHAT = "enter_chat"
_UPLOAD_CHUNK_SIZE = 512 * 1024


class WeComWsClient:
    def __init__(self, hass: HomeAssistant, bot_id: str, secret: str) -> None:
        self.hass = hass
        self.bot_id = bot_id
        self.secret = secret
        self._session = async_get_clientsession(hass)
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._runner_task: asyncio.Task[None] | None = None
        self._running = False
        self._authenticated = False
        self._callback: Any = None
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}

    @property
    def status(self) -> str:
        if self._authenticated:
            return "authenticated"
        if self._ws is not None and not self._ws.closed:
            return "connected"
        return "disconnected"

    def set_message_callback(self, callback: Any) -> None:
        self._callback = callback

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._runner_task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._running = False
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()
        if self._runner_task:
            self._runner_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._runner_task
            self._runner_task = None
        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None
        self._authenticated = False

    async def send_markdown(self, target: str, message: str) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        payload = {
            "cmd": CMD_SEND_MSG,
            "headers": {"req_id": f"{CMD_SEND_MSG}_{uuid.uuid4().hex[:16]}"},
            "body": {"chatid": target, "msgtype": "markdown", "markdown": {"content": message}},
        }
        await self._ws.send_json(payload)

    async def send_text(self, target: str, message: str, mentioned_list: list[str] | None = None) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        text_body: dict[str, Any] = {"content": message}
        if mentioned_list:
            text_body["mentioned_list"] = mentioned_list
        payload = {
            "cmd": CMD_SEND_MSG,
            "headers": {"req_id": f"{CMD_SEND_MSG}_{uuid.uuid4().hex[:16]}"},
            "body": {"chatid": target, "msgtype": "text", "text": text_body},
        }
        await self._ws.send_json(payload)

    async def reply_markdown(self, callback_req_id: str, message: str) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        payload = {
            "cmd": CMD_RESPOND_MSG,
            "headers": {"req_id": callback_req_id},
            "body": {"msgtype": "markdown", "markdown": {"content": message}},
        }
        await self._ws.send_json(payload)

    async def reply_welcome(self, callback_req_id: str, message: str) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        payload = {
            "cmd": CMD_RESPOND_WELCOME,
            "headers": {"req_id": callback_req_id},
            "body": {"msgtype": "markdown", "markdown": {"content": message}},
        }
        await self._ws.send_json(payload)

    async def reply_via_response_url(self, response_url: str, message: str) -> None:
        payload = {"msgtype": "markdown", "markdown": {"content": message}}
        async with self._session.post(response_url, json=payload, timeout=15) as resp:
            _ = await resp.text()

    async def send_image(self, target: str, image_bytes: bytes) -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        if not image_bytes:
            raise ValueError("wecom image data is empty")
        media_id = await self._upload_media(image_bytes, media_type="image", filename="camera.jpg")
        payload = {
            "cmd": CMD_SEND_MSG,
            "headers": {"req_id": f"{CMD_SEND_MSG}_{uuid.uuid4().hex[:16]}"},
            "body": {"chatid": target, "msgtype": "image", "image": {"media_id": media_id}},
        }
        await self._send_with_reply(payload)

    async def send_video(self, target: str, video_bytes: bytes, filename: str = "video.mp4") -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        if not video_bytes:
            raise ValueError("wecom video data is empty")
        media_id = await self._upload_media(video_bytes, media_type="video", filename=filename)
        payload = {
            "cmd": CMD_SEND_MSG,
            "headers": {"req_id": f"{CMD_SEND_MSG}_{uuid.uuid4().hex[:16]}"},
            "body": {"chatid": target, "msgtype": "video", "video": {"media_id": media_id}},
        }
        await self._send_with_reply(payload)

    async def send_file(self, target: str, file_bytes: bytes, filename: str = "file") -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        if not file_bytes:
            raise ValueError("wecom file data is empty")
        media_id = await self._upload_media(file_bytes, media_type="file", filename=filename)
        payload = {
            "cmd": CMD_SEND_MSG,
            "headers": {"req_id": f"{CMD_SEND_MSG}_{uuid.uuid4().hex[:16]}"},
            "body": {"chatid": target, "msgtype": "file", "file": {"media_id": media_id}},
        }
        await self._send_with_reply(payload)

    async def send_voice(self, target: str, voice_bytes: bytes, filename: str = "voice.mp3") -> None:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        if not voice_bytes:
            raise ValueError("wecom voice data is empty")
        media_id = await self._upload_media(voice_bytes, media_type="voice", filename=filename)
        payload = {
            "cmd": CMD_SEND_MSG,
            "headers": {"req_id": f"{CMD_SEND_MSG}_{uuid.uuid4().hex[:16]}"},
            "body": {"chatid": target, "msgtype": "voice", "voice": {"media_id": media_id}},
        }
        await self._send_with_reply(payload)

    async def _send_with_reply(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self._ws or self._ws.closed:
            raise RuntimeError("websocket not connected")
        req_id = str(payload.get("headers", {}).get("req_id") or "").strip()
        if not req_id:
            raise ValueError("req_id is required")
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[req_id] = future
        try:
            await self._ws.send_json(payload)
            frame = await asyncio.wait_for(future, timeout=15)
        finally:
            self._pending.pop(req_id, None)
        if isinstance(frame.get("errcode"), int) and frame["errcode"] != 0:
            raise RuntimeError(f"wecom {payload.get('cmd')} failed: {frame.get('errcode')} {frame.get('errmsg')}")
        return frame

    async def _upload_media(self, file_bytes: bytes, *, media_type: str, filename: str) -> str:
        total_size = len(file_bytes)
        total_chunks = max(1, (total_size + _UPLOAD_CHUNK_SIZE - 1) // _UPLOAD_CHUNK_SIZE)
        init_frame = await self._send_with_reply(
            {
                "cmd": CMD_UPLOAD_MEDIA_INIT,
                "headers": {"req_id": f"{CMD_UPLOAD_MEDIA_INIT}_{uuid.uuid4().hex[:16]}"},
                "body": {
                    "type": media_type,
                    "filename": filename,
                    "total_size": total_size,
                    "total_chunks": total_chunks,
                    "md5": hashlib.md5(file_bytes).hexdigest(),
                },
            }
        )
        upload_id = str((init_frame.get("body") or {}).get("upload_id") or "")
        if not upload_id:
            raise ValueError("wecom upload init missing upload_id")

        for chunk_index in range(total_chunks):
            start = chunk_index * _UPLOAD_CHUNK_SIZE
            end = min(start + _UPLOAD_CHUNK_SIZE, total_size)
            chunk = file_bytes[start:end]
            await self._send_with_reply(
                {
                    "cmd": CMD_UPLOAD_MEDIA_CHUNK,
                    "headers": {"req_id": f"{CMD_UPLOAD_MEDIA_CHUNK}_{uuid.uuid4().hex[:16]}"},
                    "body": {
                        "upload_id": upload_id,
                        "chunk_index": chunk_index,
                        "base64_data": base64.b64encode(chunk).decode("ascii"),
                    },
                }
            )

        finish_frame = await self._send_with_reply(
            {
                "cmd": CMD_UPLOAD_MEDIA_FINISH,
                "headers": {"req_id": f"{CMD_UPLOAD_MEDIA_FINISH}_{uuid.uuid4().hex[:16]}"},
                "body": {"upload_id": upload_id},
            }
        )
        media_id = str((finish_frame.get("body") or {}).get("media_id") or "")
        if not media_id:
            raise ValueError("wecom upload finish missing media_id")
        return media_id

    async def _run(self) -> None:
        retry_count = 0
        max_retries = 8
        while self._running:
            try:
                self._ws = await self._session.ws_connect(WS_URL, heartbeat=60)
                await self._ws.send_json(
                    {
                        "cmd": CMD_SUBSCRIBE,
                        "headers": {"req_id": f"{CMD_SUBSCRIBE}_{uuid.uuid4().hex[:16]}"},
                        "body": {"bot_id": self.bot_id, "secret": self.secret},
                    }
                )
                self._authenticated = True
                retry_count = 0
                while self._running and self._ws and not self._ws.closed:
                    msg = await self._ws.receive()
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        frame = json.loads(msg.data)
                        req_id = str(frame.get("headers", {}).get("req_id") or "").strip()
                        if req_id and req_id in self._pending:
                            future = self._pending[req_id]
                            if not future.done():
                                future.set_result(frame)
                            continue
                        if self._callback:
                            await self._callback(frame)
                    elif msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED):
                        break
            except asyncio.CancelledError:
                raise
            except Exception as err:
                retry_count += 1
                _LOGGER.warning("WeCom websocket error (attempt %d/%d): %s", retry_count, max_retries, err)
                if retry_count >= max_retries:
                    _LOGGER.error("WeCom connection failed after %d attempts, stopping", max_retries)
                    self._running = False
                    break
            finally:
                self._authenticated = False
                if self._ws and not self._ws.closed:
                    await self._ws.close()
                self._ws = None
            if self._running:
                await asyncio.sleep(5)


def _extract_text(body: dict[str, Any]) -> str:
    if body.get("msgtype") == "text":
        return body.get("text", {}).get("content", "").strip()
    if body.get("msgtype") == "voice":
        content = str((body.get("voice") or {}).get("content") or "").strip()
        return content
    return str(body.get("content", "")).strip()


def _extract_reply_target(body: dict[str, Any]) -> str:
    sender = body.get("from", {})
    return sender.get("userid") or body.get("from_userid") or body.get("userid") or body.get("chatid") or "@all"


async def async_validate_config(_: HomeAssistant, config: dict[str, Any]) -> None:
    bot_id = str(config.get(CONF_WECOM_BOT_ID, "")).strip()
    secret = str(config.get(CONF_WECOM_SECRET, "")).strip()
    if not bot_id or not secret:
        raise ValueError("bot_id and secret are required")


async def _resolve_media(hass: HomeAssistant, source: str) -> bytes | None:
    source = im_public_url_for_source(hass, source)
    if source.startswith(("http://", "https://")):
        session = async_get_clientsession(hass)
        async with session.get(source) as resp:
            if resp.status == 200:
                return await resp.read()
    elif await hass.async_add_executor_job(os.path.isfile, source):
        return await hass.async_add_executor_job(_read_file, source)
    return None


def _read_file(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


async def _resolve_image(hass: HomeAssistant, source: str) -> bytes | None:
    from ...media.camera import async_resolve_camera_entity
    resolved = await async_resolve_camera_entity(hass, source)
    if resolved is not None:
        from homeassistant.components.camera import async_get_image
        image = await async_get_image(hass, resolved)
        return image.content
    return await _resolve_media(hass, source)


async def _resolve_video(hass: HomeAssistant, source: str) -> tuple[bytes, str] | None:
    from ...media.camera import async_resolve_camera_entity, async_record_camera_clip
    resolved = await async_resolve_camera_entity(hass, source)
    if resolved is not None:
        return await async_record_camera_clip(hass, resolved)
    data = await _resolve_media(hass, source)
    if data:
        name = source.rsplit("/", 1)[-1] or "video.mp4"
        return data, name
    return None


def _format_live_progress(payload: dict[str, Any]) -> str:
    display_text = str(payload.get("display_text") or "").strip()
    if display_text:
        cleaned = display_text.replace("┊", "").replace("*", "").strip()
        return cleaned[:200]
    tool_name = str(payload.get("tool_name") or "").strip()
    if tool_name:
        return f"🔧 {tool_name}"
    return ""


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, Any],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    bot_id = str(config.get(CONF_WECOM_BOT_ID, "")).strip()
    secret = str(config.get(CONF_WECOM_SECRET, "")).strip()
    show_live_progress = bool(config.get(_CONF_WECOM_SHOW_LIVE_PROGRESS, False))
    client = WeComWsClient(hass, bot_id, secret)
    tracker = await async_get_tracker(hass, subentry_id)

    async def _reply_text(target: str, text: str, response_url: str, callback_req_id: str) -> None:
        if response_url:
            with contextlib.suppress(Exception):
                await client.reply_via_response_url(response_url, text)
                return
        if callback_req_id:
            with contextlib.suppress(Exception):
                await client.reply_markdown(callback_req_id, text)
                return
        with contextlib.suppress(Exception):
            await client.send_markdown(target, text)

    async def _handle_inbound(frame: dict[str, Any]) -> None:
        cmd = frame.get("cmd")
        if cmd not in (CMD_MSG_CALLBACK, CMD_EVENT_CALLBACK):
            return
        callback_req_id = frame.get("headers", {}).get("req_id", "")
        body = frame.get("body", {})
        response_url = body.get("response_url", "")

        if cmd == CMD_EVENT_CALLBACK:
            event_type = body.get("event", {}).get("eventtype") or body.get("eventtype")
            if event_type == EVENT_ENTER_CHAT and callback_req_id:
                with contextlib.suppress(Exception):
                    await client.reply_welcome(callback_req_id, "已连接 Home Assistant，你可以直接发送问题或控制指令。")
            return

        text = _extract_text(body)
        if not text:
            return

        target = _extract_reply_target(body)
        sender_name = str(body.get("sender_name") or body.get("chat_name") or target)
        await tracker.async_record(
            provider=PROVIDER_WECOM,
            target=target,
            target_type="chatid",
            display_name=sender_name,
        )
        try:
            command = parse_command(text)
        except ValueError as err:
            await _reply_text(target, f"Invalid command: {err}", response_url, callback_req_id)
            return
        if command is None:
            return

        async def _run_live_progress_bridge(conversation_id: str) -> None:
            if not show_live_progress:
                await asyncio.Future()

            queue: asyncio.Queue[str] = asyncio.Queue()

            @callback
            def _listener(event) -> None:
                payload = event.data or {}
                if payload.get("conversation_id") != conversation_id:
                    return
                text = _format_live_progress(payload)
                if text:
                    queue.put_nowait(text)

            unsub = hass.bus.async_listen(EVENT_LIVE_PROGRESS, _listener)
            last_sent = ""
            pending_tasks: list[asyncio.Task] = []
            
            async def _fire_and_forget(msg: str) -> None:
                with contextlib.suppress(Exception):
                    await _reply_text(target, msg, response_url, callback_req_id)
            
            try:
                while True:
                    progress_text = await queue.get()
                    if progress_text == last_sent:
                        continue
                    task = asyncio.create_task(_fire_and_forget(progress_text))
                    pending_tasks.append(task)
                    last_sent = progress_text
            except asyncio.CancelledError:
                if pending_tasks:
                    await asyncio.gather(*pending_tasks, return_exceptions=True)
            finally:
                unsub()

        conversation_id = f"wecom:{target}"
        progress_task = asyncio.create_task(_run_live_progress_bridge(conversation_id))

        try:
            reply = await execute_command(
                hass,
                command,
                conversation_id=conversation_id,
                agent_id=agent_id,
                extra_system_prompt=build_wecom_prompt(),
                user_id=sender_name,
            )
        except Exception as err:
            reply = f"Execution failed: {type(err).__name__}"
            _LOGGER.exception("WeCom command execution failed: %s", err)
        finally:
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task

        if not reply:
            return

        segments = parse_reply_segments(reply)
        for seg in segments:
            if isinstance(seg, TextSegment):
                await _reply_text(target, seg.text, response_url, callback_req_id)
            elif isinstance(seg, ImageSegment):
                try:
                    image_bytes = await _resolve_image(hass, seg.source)
                    if image_bytes:
                        await client.send_image(target, image_bytes)
                except Exception as err:
                    _LOGGER.warning("WeCom image send failed: %s", err)
                    await _reply_text(target, f"Image send failed: {err}", response_url, callback_req_id)
            elif isinstance(seg, VideoSegment):
                try:
                    result = await _resolve_video(hass, seg.source)
                    if result:
                        video_bytes, file_name = result
                        await client.send_video(target, video_bytes, file_name)
                    else:
                        await _reply_text(target, f"📎 {seg.source}", response_url, callback_req_id)
                except Exception as err:
                    _LOGGER.warning("WeCom video send failed: %s", err)
                    await _reply_text(target, f"📎 {seg.source}", response_url, callback_req_id)
            elif isinstance(seg, FileSegment):
                try:
                    media_bytes = await _resolve_media(hass, seg.source)
                    if media_bytes:
                        name = seg.source.rsplit("/", 1)[-1] or "file"
                        await client.send_file(target, media_bytes, name)
                    else:
                        await _reply_text(target, f"📎 {seg.source}", response_url, callback_req_id)
                except Exception as err:
                    _LOGGER.warning("WeCom file send failed: %s", err)
                    await _reply_text(target, f"📎 {seg.source}", response_url, callback_req_id)
            elif isinstance(seg, VoiceSegment):
                try:
                    from ...media.tts import async_generate_tts_mp3, is_edge_tts_available
                    if is_edge_tts_available():
                        mp3_bytes = await async_generate_tts_mp3(hass, seg.text)
                        await client.send_voice(target, mp3_bytes, "voice.mp3")
                except Exception as err:
                    _LOGGER.warning("WeCom voice send failed: %s", err)
                    await _reply_text(target, seg.text, response_url, callback_req_id)

    client.set_message_callback(_handle_inbound)
    await client.start()

    async def _send(target: str, message: str, _: str) -> None:
        await client.send_markdown(target or "@all", message)

    async def _send_image(target: str, image_bytes: bytes, _: str) -> None:
        await client.send_image(target or "@all", image_bytes)

    async def _send_video(target: str, video_bytes: bytes, filename: str, _: str) -> None:
        await client.send_video(target or "@all", video_bytes, filename)

    async def _send_file(target: str, file_bytes: bytes, filename: str, _: str) -> None:
        await client.send_file(target or "@all", file_bytes, filename)

    async def _send_voice(target: str, voice_bytes: bytes, filename: str, _: str) -> None:
        await client.send_voice(target or "@all", voice_bytes, filename)

    return ProviderRuntime(
        key=PROVIDER_WECOM,
        title=PROVIDER_WECOM,
        subentry_id=subentry_id,
        client=client,
        stop=client.stop,
        send_text=_send,
        status=lambda: client.status,
        known_targets=tracker.snapshot,
        selected_target=tracker.selected_target,
        select_target=tracker.async_select_target,
        send_image=_send_image,
        send_video=_send_video,
        send_file=_send_file,
        send_voice=_send_voice,
    )


_CONF_WECOM_SHOW_LIVE_PROGRESS = "wecom_show_live_progress"


def _build_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_WECOM_BOT_ID, default=current.get(CONF_WECOM_BOT_ID, "")): str,
            vol.Required(CONF_WECOM_SECRET, default=current.get(CONF_WECOM_SECRET, "")): str,
            vol.Optional(_CONF_WECOM_SHOW_LIVE_PROGRESS, default=current.get(_CONF_WECOM_SHOW_LIVE_PROGRESS, False)): bool,
        }
    )


PROVIDER_SPEC = ProviderSpec(
    key=PROVIDER_WECOM,
    schema_builder=_build_schema,
    validate_config=async_validate_config,
    setup_provider=async_setup_provider,
    allow_multiple=True,
)
