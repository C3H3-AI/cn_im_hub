from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import mimetypes
from pathlib import Path
from typing import Any
import uuid

from homeassistant.core import HomeAssistant, callback
import voluptuous as vol

EVENT_LIVE_PROGRESS = "ha_crack_live_progress"

from ...core.command import execute_command, parse_command
from ...core.known_targets import async_get_tracker
from ...const import (
    CONF_FEISHU_APP_ID,
    CONF_FEISHU_APP_SECRET,
    CONF_FEISHU_VERIFICATION_TOKEN,
    DEFAULT_FEISHU_TARGET_TYPE,
    DOMAIN,
    PROVIDER_FEISHU,
)
from ...media.card import parse_card_source
from ...media.rich_media import (
    CardSegment,
    FileSegment,
    GifSegment,
    ImageSegment,
    TextSegment,
    VideoSegment,
    VoiceSegment,
    extract_reply_prefix,
    parse_reply_segments,
)
from .prompt import build_feishu_prompt
from ...models import ProviderRuntime
from ..base import ProviderSpec
from .flow import FeishuScanSubentryFlow
from .api import FeishuApiClient
from .card import (
    build_feishu_card,
    build_final_card,
    build_paginated_cards,
    build_progress_card,
    build_response_card,
    mark_claw,
)
from .ws import FeishuWsClient

_LOGGER = logging.getLogger(__name__)

_TMP_DIR = "cn_im_hub"


async def async_validate_config(hass: HomeAssistant, config: dict[str, Any]) -> None:
    app_id, app_secret = _credentials(config)
    if not app_id or not app_secret:
        raise ValueError("app_id and app_secret are required")
    await FeishuApiClient(hass, app_id, app_secret).async_validate_connection()


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, Any],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    app_id, app_secret = _credentials(config)
    show_live_progress = bool(config.get(_CONF_FEISHU_SHOW_LIVE_PROGRESS, False))

    # Use channel-specific agent_id if configured, otherwise use global agent_id
    channel_agent_id = str(config.get(CONF_CHANNEL_AGENT_ID, "")).strip()
    effective_agent_id = channel_agent_id if channel_agent_id else agent_id

    api = FeishuApiClient(hass, app_id, app_secret)
    await api.async_validate_connection()
    hass.data.setdefault(DOMAIN, {}).setdefault("_feishu_api_clients", {})[subentry_id] = api
    tracker = await async_get_tracker(hass, subentry_id)
    ws = FeishuWsClient(
        hass=hass,
        app_id=app_id,
        app_secret=app_secret,
        message_handler=_message_handler_factory(hass, api, tracker, effective_agent_id, show_live_progress),
    )
    await ws.async_start()
    return _runtime_factory(ws, api, tracker, subentry_id, app_id)


def _credentials(config: dict[str, Any]) -> tuple[str, str]:
    return str(config.get(CONF_FEISHU_APP_ID, "")).strip(), str(config.get(CONF_FEISHU_APP_SECRET, "")).strip()


def _format_live_progress(payload: dict[str, Any]) -> str:
    display_text = str(payload.get("display_text") or "").strip()
    if display_text:
        cleaned = display_text.replace("┊", "").replace("*", "").strip()
        return cleaned[:200]
    tool_name = str(payload.get("tool_name") or "").strip()
    if tool_name:
        return f"🔧 {tool_name}"
    return ""


def _message_handler_factory(hass, api, tracker, agent_id, show_live_progress: bool = False):
    async def _run_live_progress_bridge(conversation_id: str, receive_id: str, receive_type: str) -> None:
        if not show_live_progress:
            return  # No progress needed, task finishes immediately

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
                await _reply(api, receive_id, receive_type, msg)

        try:
            while True:
                text = await queue.get()
                if text == last_sent:
                    continue
                task = asyncio.create_task(_fire_and_forget(text))
                pending_tasks.append(task)
                last_sent = text
        except asyncio.CancelledError:
            while not queue.empty():
                text = queue.get_nowait()
                if text and text != last_sent:
                    task = asyncio.create_task(_fire_and_forget(text))
                    pending_tasks.append(task)
                    last_sent = text
            if pending_tasks:
                await asyncio.gather(*pending_tasks, return_exceptions=True)
        finally:
            unsub()

    async def _handle_message(message: dict[str, str]) -> None:
        chat_id = message.get("chat_id", "")
        user_id = message.get("user_id", "")
        text = message.get("text", "").strip()
        msg_type = message.get("msg_type", "")
        raw_content = message.get("raw_content", "")
        parent_id = message.get("parent_id", "")

        receive_id = chat_id or user_id
        receive_type = "chat_id" if chat_id else "open_id"
        if not receive_id:
            return

        # Record known target for future reference
        await tracker.async_record(
            provider=PROVIDER_FEISHU,
            target=receive_id,
            target_type=receive_type,
            display_name=user_id or chat_id or receive_id,
        )

        # Process attachments (image, file, audio, media, sticker)
        processed_text = text
        if msg_type in ("image", "file", "audio", "media", "sticker") and raw_content:
            attachment_tag = await _process_attachment(hass, api, raw_content, msg_type, message.get("message_id", ""))
            if attachment_tag:
                processed_text = (processed_text + "\n" + attachment_tag) if processed_text else attachment_tag

        # Resolve reference/quote message if parent_id is present
        if parent_id:
            ref_text = await _resolve_reference(hass, api, parent_id)
            if ref_text:
                ref_block = f"[引用消息开始]\n{ref_text}\n[引用消息结束]"
                processed_text = f"{ref_block}\n{processed_text}" if processed_text else ref_block

        if not processed_text:
            return

        try:
            command = parse_command(processed_text)
        except ValueError as err:
            await _reply(api, receive_id, receive_type, f"Invalid command: {err}")
            return
        if command is None:
            return

        conversation_id = f"feishu:{receive_id}"
        progress_task = asyncio.create_task(_run_live_progress_bridge(conversation_id, receive_id, receive_type))

        try:
            result = await execute_command(
                hass,
                command,
                conversation_id=conversation_id,
                agent_id=agent_id,
                extra_system_prompt=build_feishu_prompt(),
                user_id=user_id or receive_id,
            )
        except Exception as err:
            result = f"Execution failed: {type(err).__name__}"
            _LOGGER.exception("Feishu command execution failed: %s", err)
        finally:
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task
        reply = str(result)
        if not reply:
            return
        prefix_name, reply_body = extract_reply_prefix(reply)
        card_title = prefix_name or "Claw Assistant"
        segments = parse_reply_segments(reply_body)
        for seg in segments:
            if isinstance(seg, TextSegment):
                cards = build_paginated_cards(seg.text, title=card_title)
                for card in cards:
                    await _reply(api, receive_id, receive_type, seg.text, mark_claw(card))
            elif isinstance(seg, ImageSegment):
                try:
                    image_bytes = await _resolve_image(hass, seg.source)
                    if image_bytes:
                        await api.async_send_image_message(
                            receive_id=receive_id,
                            image_bytes=image_bytes,
                            receive_id_type=receive_type,
                        )
                except Exception as err:
                    _LOGGER.warning("Feishu image send failed: %s", err)
                    await _reply(api, receive_id, receive_type, f"Image send failed: {err}")
            elif isinstance(seg, CardSegment):
                card_spec = parse_card_source(seg.source)
                if card_spec:
                    feishu_card = build_feishu_card(card_spec, title=card_title)
                    await _reply(api, receive_id, receive_type, card_spec.text or " ", mark_claw(feishu_card))
                else:
                    await _reply(api, receive_id, receive_type, f"Invalid card: {seg.source[:100]}")
            elif isinstance(seg, VideoSegment):
                try:
                    result = await _resolve_video(hass, seg.source)
                    if result:
                        video_bytes, file_name = result
                        await api.async_send_video_message(
                            receive_id=receive_id,
                            video_bytes=video_bytes,
                            file_name=file_name,
                            receive_id_type=receive_type,
                        )
                    else:
                        await _reply(api, receive_id, receive_type, f"📎 {seg.source}")
                except Exception as err:
                    _LOGGER.warning("Feishu video send failed: %s", err)
                    await _reply(api, receive_id, receive_type, f"📎 {seg.source}")
            elif isinstance(seg, GifSegment):
                try:
                    result = await _resolve_gif(hass, seg.source)
                    if result:
                        gif_bytes, file_name = result
                        await api.async_send_image_message(
                            receive_id=receive_id,
                            image_bytes=gif_bytes,
                            receive_id_type=receive_type,
                        )
                    else:
                        await _reply(api, receive_id, receive_type, f"📎 {seg.source}")
                except Exception as err:
                    _LOGGER.warning("Feishu gif send failed: %s", err)
                    await _reply(api, receive_id, receive_type, f"📎 {seg.source}")
            elif isinstance(seg, FileSegment):
                try:
                    media_bytes = await _resolve_media(hass, seg.source)
                    if media_bytes:
                        name = seg.source.rsplit("/", 1)[-1] or "file"
                        await api.async_send_file_message(
                            receive_id=receive_id,
                            file_bytes=media_bytes,
                            file_name=name,
                            receive_id_type=receive_type,
                        )
                    else:
                        await _reply(api, receive_id, receive_type, f"📎 {seg.source}")
                except Exception as err:
                    _LOGGER.warning("Feishu file send failed: %s", err)
                    await _reply(api, receive_id, receive_type, f"📎 {seg.source}")
            elif isinstance(seg, VoiceSegment):
                try:
                    from ...media.tts import async_generate_tts_mp3, is_edge_tts_available
                    if is_edge_tts_available():
                        mp3_bytes = await async_generate_tts_mp3(hass, seg.text)
                        await api.async_send_file_message(
                            receive_id=receive_id,
                            file_bytes=mp3_bytes,
                            file_name="voice.mp3",
                            receive_id_type=receive_type,
                        )
                except Exception as err:
                    _LOGGER.warning("Feishu voice send failed: %s", err)
                    await _reply(api, receive_id, receive_type, seg.text)
    return _handle_message


async def _process_attachment(
    hass: HomeAssistant,
    api: FeishuApiClient,
    raw_content: str,
    msg_type: str,
    message_id: str,
) -> str:
    """Download Feishu attachment and return [ATTACHMENT] tag.

    Returns empty string if download fails or type is unsupported.
    """
    try:
        payload = json.loads(raw_content)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""

    if msg_type == "image":
        image_key = str(payload.get("image_key") or "")
        if not image_key:
            return ""
        image_bytes = await api.async_download_image(image_key)
        if not image_bytes:
            return ""
        mime, path = await _save_attachment(hass, data=image_bytes, mime_hint="image/png", prefix="img")
        return f"[ATTACHMENT:{mime}:{path}]"

    if msg_type == "file":
        file_key = str(payload.get("file_key") or "")
        file_name = str(payload.get("file_name") or "file")
        if not file_key:
            return ""
        result = await api.async_download_file(file_key)
        if not result:
            return ""
        file_bytes, remote_name = result
        name = remote_name or file_name
        mime_hint = mimetypes.guess_type(name)[0] or "application/octet-stream"
        mime, path = await _save_attachment(hass, data=file_bytes, mime_hint=mime_hint, prefix="file")
        return f"[ATTACHMENT:{mime}:{path}]"

    if msg_type == "audio":
        file_key = str(payload.get("file_key") or "")
        if not file_key or not message_id:
            return ""
        audio_bytes = await api.async_download_resource(message_id, file_key, file_type="file")
        if not audio_bytes:
            return ""
        mime, path = await _save_attachment(hass, data=audio_bytes, mime_hint="audio/ogg", prefix="audio")
        return f"[ATTACHMENT:{mime}:{path}]"

    if msg_type == "media":
        file_key = str(payload.get("file_key") or "")
        file_name = str(payload.get("file_name") or "video.mp4")
        if not file_key:
            return ""
        media_bytes = await api.async_download_file(file_key)
        if media_bytes:
            file_bytes, remote_name = media_bytes
            name = remote_name or file_name
            mime_hint = mimetypes.guess_type(name)[0] or "video/mp4"
            mime, path = await _save_attachment(hass, data=file_bytes, mime_hint=mime_hint, prefix="video")
            return f"[ATTACHMENT:{mime}:{path}]"
        return ""

    if msg_type == "sticker":
        file_key = str(payload.get("file_key") or "")
        if not file_key:
            return ""
        sticker_bytes = await api.async_download_file(file_key)
        if sticker_bytes:
            file_bytes, _ = sticker_bytes
            mime, path = await _save_attachment(hass, data=file_bytes, mime_hint="image/png", prefix="sticker")
            return f"[ATTACHMENT:{mime}:{path}]"
        return ""

    return ""


async def _save_attachment(
    hass: HomeAssistant,
    data: bytes,
    mime_hint: str,
    prefix: str,
) -> tuple[str, str]:
    """Save attachment data to temp dir and return (mime, path)."""
    tmp_dir = Path(hass.config.path(".storage", _TMP_DIR, "tmp"))
    await hass.async_add_executor_job(tmp_dir.mkdir, True, True)  # exist_ok=True

    # Determine file extension from mime hint
    ext = mimetypes.guess_extension(mime_hint) or ".bin"
    if ext == ".jpe":
        ext = ".jpg"
    elif ext == ".oga":
        ext = ".ogg"

    file_name = f"feishu_{prefix}_{uuid.uuid4().hex[:12]}{ext}"
    file_path = str(tmp_dir / file_name)
    await hass.async_add_executor_job(Path(file_path).write_bytes, data)
    return mime_hint, file_path


async def _resolve_reference(hass: HomeAssistant, api: FeishuApiClient, parent_id: str) -> str:
    """Get the text content of a referenced message for quote context."""
    try:
        msg = await api.async_get_message(parent_id)
        if not msg:
            return ""
        msg_type = str(msg.get("msg_type") or "")
        content = str(msg.get("content") or "")
        if not content:
            return ""
        from .ws import _extract_text
        return _extract_text(content, msg_type)
    except Exception as err:
        _LOGGER.warning("Feishu reference resolve failed: %s", err)
        return ""


async def _resolve_media(hass: HomeAssistant, source: str) -> bytes | None:
    import os
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
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
    _LOGGER.debug("_resolve_image source=%s resolved=%s", source, resolved)
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


async def _resolve_gif(hass: HomeAssistant, source: str) -> tuple[bytes, str] | None:
    from ...media.camera import async_resolve_camera_entity, async_capture_camera_gif
    resolved = await async_resolve_camera_entity(hass, source)
    if resolved is not None:
        return await async_capture_camera_gif(hass, resolved)
    data = await _resolve_media(hass, source)
    if data:
        name = source.rsplit("/", 1)[-1] or "image.gif"
        return data, name
    return None


async def _reply(api: FeishuApiClient, receive_id: str, receive_type: str, text: str, card: dict[str, Any] | None = None) -> None:
    if card:
        try:
            await api.async_send_card_message(receive_id=receive_id, card=card, receive_id_type=receive_type)
            return
        except Exception as err:
            _LOGGER.warning("Card send failed, falling back to text: %s", err)
    await api.async_send_safe_reply(receive_id=receive_id, receive_id_type=receive_type, text=text)


def _runtime_factory(ws, api, tracker, subentry_id: str, app_id: str = "") -> ProviderRuntime:
    async def _send(target: str, message: str, target_type: str) -> None:
        await api.async_send_text_message(receive_id=target, text=message, receive_id_type=target_type or DEFAULT_FEISHU_TARGET_TYPE)

    async def _send_image(target: str, image_bytes: bytes, target_type: str) -> None:
        await api.async_send_image_message(receive_id=target, image_bytes=image_bytes, receive_id_type=target_type or DEFAULT_FEISHU_TARGET_TYPE)

    async def _send_video(target: str, video_bytes: bytes, filename: str, target_type: str) -> None:
        await api.async_send_video_message(receive_id=target, video_bytes=video_bytes, file_name=filename, receive_id_type=target_type or DEFAULT_FEISHU_TARGET_TYPE)

    async def _send_file(target: str, file_bytes: bytes, filename: str, target_type: str) -> None:
        await api.async_send_file_message(receive_id=target, file_bytes=file_bytes, file_name=filename, receive_id_type=target_type or DEFAULT_FEISHU_TARGET_TYPE)

    async def _send_card(target: str, card: dict[str, Any], target_type: str) -> None:
        await api.async_send_card_message(receive_id=target, card=card, receive_id_type=target_type or DEFAULT_FEISHU_TARGET_TYPE)

    return ProviderRuntime(
        key=PROVIDER_FEISHU,
        title=PROVIDER_FEISHU,
        subentry_id=subentry_id,
        client=ws,
        stop=ws.async_stop,
        send_text=_send,
        status=lambda: ws.status,
        known_targets=tracker.snapshot,
        selected_target=tracker.selected_target,
        select_target=tracker.async_select_target,
        send_image=_send_image,
        send_video=_send_video,
        send_file=_send_file,
        send_card=_send_card,
    )


_CONF_FEISHU_SHOW_LIVE_PROGRESS = "feishu_show_live_progress"
CONF_CHANNEL_AGENT_ID = "channel_agent_id"  # Per-channel agent override


def _build_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_FEISHU_APP_ID, default=current.get(CONF_FEISHU_APP_ID, "")): str,
            vol.Required(CONF_FEISHU_APP_SECRET, default=current.get(CONF_FEISHU_APP_SECRET, "")): str,
            vol.Optional(CONF_FEISHU_VERIFICATION_TOKEN, default=current.get(CONF_FEISHU_VERIFICATION_TOKEN, "")): str,
            vol.Optional(_CONF_FEISHU_SHOW_LIVE_PROGRESS, default=current.get(_CONF_FEISHU_SHOW_LIVE_PROGRESS, False)): bool,
        }
    )


PROVIDER_SPEC = ProviderSpec(
    key=PROVIDER_FEISHU,
    schema_builder=_build_schema,
    validate_config=async_validate_config,
    setup_provider=async_setup_provider,
    allow_multiple=True,
    flow_handler=FeishuScanSubentryFlow,
)