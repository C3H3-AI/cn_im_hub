from __future__ import annotations

import json as _json
import logging
from pathlib import Path
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from ..const import (
    ATTR_APPROVAL_ID,
    ATTR_BCC,
    ATTR_CAMERA_ENTITY,
    ATTR_CARD_BUTTONS,
    ATTR_CARD_CONTENT,
    ATTR_CARD_JSON,
    ATTR_CARD_TITLE,
    ATTR_CC,
    ATTR_CHANNEL,
    ATTR_CURSOR,
    ATTR_FILE_NAME,
    ATTR_FILE_PATH,
    ATTR_FILE_URL,
    ATTR_FOLDER,
    ATTR_GIF_FPS,
    ATTR_INCLUDE_ATTACHMENTS,
    ATTR_LIMIT,
    ATTR_LOOKBACK,
    ATTR_MESSAGE,
    ATTR_MESSAGE_FORMAT,
    ATTR_MESSAGE_ID,
    ATTR_MEDIA_TYPE,
    ATTR_PERMANENT,
    ATTR_QUERY,
    ATTR_RECORD_DURATION,
    ATTR_REPLY_ALL,
    ATTR_SEARCH_IN,
    ATTR_SUBJECT,
    ATTR_TARGET,
    ATTR_TTS_TEXT,
    ATTR_WECHAT_ACCOUNT_ID,
    CHANNEL_FEISHU_CHAT_ID,
    CHANNEL_OPTIONS,
    DEFAULT_GIF_DURATION,
    DEFAULT_VIDEO_RECORD_DURATION,
    DOMAIN,
    MAIL_FOLDERS,
    MAIL_SEARCH_IN,
    PROVIDER_AGENT_MAIL,
    PROVIDER_WECHAT,
    SERVICE_DELETE_MESSAGE,
    SERVICE_FORWARD_MESSAGE,
    SERVICE_LIST_MESSAGES,
    SERVICE_READ_MESSAGE,
    SERVICE_SEARCH_MESSAGES,
    SERVICE_SEND_MESSAGE,
    SERVICE_REPLY_MESSAGE,
)
from ..media.camera import (
    async_capture_camera_gif,
    async_record_camera_clip,
    async_resolve_camera_entity,
    resolve_ha_local_path,
)
from .routing import (
    all_provider_runtimes,
    parse_channel,
    select_provider_runtime,
    select_wechat_runtime,
)

_LOGGER = logging.getLogger(__name__)

_SUFFIX_TYPE_MAP: dict[str, str] = {
    **dict.fromkeys((".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"), "image"),
    **dict.fromkeys((".mp3", ".wav", ".silk", ".ogg", ".amr", ".m4a"), "voice"),
    **dict.fromkeys((".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"), "video"),
}

SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_CHANNEL, default=CHANNEL_FEISHU_CHAT_ID): vol.In(CHANNEL_OPTIONS),
        vol.Optional(ATTR_MESSAGE, default=""): cv.string,
        vol.Optional(ATTR_TARGET, default=""): cv.string,
        vol.Optional(ATTR_WECHAT_ACCOUNT_ID, default=""): cv.string,
        vol.Optional(ATTR_CAMERA_ENTITY, default=""): vol.Any(None, "", cv.entity_id),
        vol.Optional(ATTR_FILE_PATH, default=""): cv.string,
        vol.Optional(ATTR_FILE_URL, default=""): cv.string,
        vol.Optional(ATTR_FILE_NAME, default=""): cv.string,
        vol.Optional(ATTR_MEDIA_TYPE, default=""): vol.Any("", vol.In(["image", "gif", "voice", "video", "file"])),
        vol.Optional(ATTR_TTS_TEXT, default=""): cv.string,
        vol.Optional(ATTR_MESSAGE_FORMAT, default=""): vol.Any("", vol.In(["auto", "text", "markdown"])),
        vol.Optional(ATTR_APPROVAL_ID, default=""): cv.string,
        vol.Optional(ATTR_RECORD_DURATION): vol.Coerce(int),
        vol.Optional(ATTR_LOOKBACK, default=0): vol.Coerce(int),
        vol.Optional(ATTR_GIF_FPS, default=2): vol.Coerce(int),
        vol.Optional(ATTR_CARD_JSON, default=""): cv.string,
        vol.Optional(ATTR_CARD_TITLE, default=""): cv.string,
        vol.Optional(ATTR_CARD_CONTENT, default=""): cv.string,
        vol.Optional(ATTR_CARD_BUTTONS, default=""): cv.string,
    }
)


def _infer_media_type(file_path: str, file_url: str, explicit: str) -> str:
    if explicit:
        return explicit
    suffix = Path((file_path or file_url).split("?", 1)[0]).suffix.lower() if (file_path or file_url) else ""
    return _SUFFIX_TYPE_MAP.get(suffix, "file")


async def _read_media_source(hass: HomeAssistant, file_path: str, file_url: str) -> tuple[bytes, str]:
    if file_path:
        path = resolve_ha_local_path(hass, file_path)
        if path is None:
            raise ValueError(f"file_path not found: {file_path}")
        return await hass.async_add_executor_job(path.read_bytes), path.name

    if file_url:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        session = async_get_clientsession(hass)
        async with session.get(file_url, timeout=60) as resp:
            if resp.status >= 400:
                raise ValueError(f"file_url download failed: {resp.status}")
            return await resp.read(), Path(file_url.split("?", 1)[0]).name or "attachment.bin"

    raise ValueError("file_path or file_url is required")


def _extract_call_data(call: ServiceCall) -> dict[str, Any]:
    d = call.data
    _s = lambda key, default="": str(d.get(key, default)).strip()
    return {
        "channel": str(d.get(ATTR_CHANNEL, CHANNEL_FEISHU_CHAT_ID)),
        "target": _s(ATTR_TARGET),
        "message": _s(ATTR_MESSAGE),
        "camera_entity": _s(ATTR_CAMERA_ENTITY),
        "file_path": _s(ATTR_FILE_PATH),
        "file_url": _s(ATTR_FILE_URL),
        "file_name": _s(ATTR_FILE_NAME),
        "media_type": _s(ATTR_MEDIA_TYPE).lower(),
        "tts_text": _s(ATTR_TTS_TEXT),
        "message_format": _s(ATTR_MESSAGE_FORMAT).lower(),
        "approval_id": _s(ATTR_APPROVAL_ID),
        "record_duration": (int(v) if (v := d.get(ATTR_RECORD_DURATION)) not in (None, "") else None),
        "lookback": int(d.get(ATTR_LOOKBACK, 0) or 0),
        "gif_fps": int(d.get(ATTR_GIF_FPS, 2) or 2),
        "wechat_account_id": _s(ATTR_WECHAT_ACCOUNT_ID),
        "card_json": _s(ATTR_CARD_JSON),
        "card_title": _s(ATTR_CARD_TITLE),
        "card_content": _s(ATTR_CARD_CONTENT),
        "card_buttons": _s(ATTR_CARD_BUTTONS),
    }


async def _handle_card(hass, provider, requested, p, resolved_target, target_type):
    card = _json.loads(p["card_json"])
    if p["camera_entity"] and requested == "feishu":
        resolved = await async_resolve_camera_entity(hass, p["camera_entity"])
        if resolved is not None:
            from homeassistant.components.camera import async_get_image
            image = await async_get_image(hass, resolved)
            if image and image.content:
                from ..providers.feishu import async_inject_camera_snapshot
                await async_inject_camera_snapshot(hass, card, image.content, provider.client)
    await provider.send_card(resolved_target, card, target_type)


async def _handle_card_simple(hass, provider, requested, p, resolved_target, target_type):
    from ..media.card import parse_card_source
    from ..providers.feishu.card import build_feishu_card, build_response_card

    content = p["card_content"]
    buttons = p["card_buttons"]

    if content or buttons:
        source = content.strip()
        if buttons:
            source = f"{source} | {buttons}" if source else buttons
        spec = parse_card_source(source)
        if spec is not None:
            card = build_feishu_card(spec, title=p["card_title"])
        elif content:
            card = build_response_card(content, title=p["card_title"])
        else:
            return
    else:
        return

    if p["camera_entity"] and requested == "feishu":
        resolved = await async_resolve_camera_entity(hass, p["camera_entity"])
        if resolved is not None:
            from homeassistant.components.camera import async_get_image
            image = await async_get_image(hass, resolved)
            if image and image.content:
                from ..providers.feishu import async_inject_camera_snapshot
                await async_inject_camera_snapshot(hass, card, image.content, provider.client)

    await provider.send_card(resolved_target, card, target_type)


async def _handle_camera(hass, provider, requested, p, resolved_target, target_type):
    resolved = await async_resolve_camera_entity(hass, p["camera_entity"])
    if resolved is None:
        raise ValueError(f"camera source not found: {p['camera_entity']}")

    handlers = {
        "video": lambda: _camera_video(hass, provider, requested, p, resolved, resolved_target, target_type),
        "gif": lambda: _camera_gif(hass, provider, requested, p, resolved, resolved_target, target_type),
    }
    is_gif = p["media_type"] == "gif" or (p["media_type"] == "image" and p["file_name"].lower().endswith(".gif"))
    handler = handlers.get("gif" if is_gif else p["media_type"])
    if handler:
        await handler()
    else:
        await _camera_snapshot(hass, provider, requested, resolved, resolved_target, target_type)
    if p["message"]:
        await provider.send_text(resolved_target, p["message"], target_type)


async def _camera_video(hass, provider, requested, p, cam, target, ttype):
    if provider.send_media is None:
        raise ValueError(f"Provider '{requested}' does not support video sending")
    video_bytes, name = await async_record_camera_clip(
        hass, cam, duration=p["record_duration"] or DEFAULT_VIDEO_RECORD_DURATION, lookback=p["lookback"],
    )
    await provider.send_media(target, video_bytes, "video", ttype, p["file_name"] or name)


async def _camera_gif(hass, provider, requested, p, cam, target, ttype):
    if provider.send_image is None:
        raise ValueError(f"Provider '{requested}' does not support GIF sending")
    gif_bytes, _ = await async_capture_camera_gif(
        hass, cam, duration=p["record_duration"] or DEFAULT_GIF_DURATION, fps=p["gif_fps"],
    )
    await provider.send_image(target, gif_bytes, ttype)


async def _camera_snapshot(hass, provider, requested, cam, target, ttype):
    if provider.send_image is None:
        raise ValueError(f"Provider '{requested}' does not support camera image sending")
    from homeassistant.components.camera import async_get_image
    image = await async_get_image(hass, cam)
    await provider.send_image(target, image.content, ttype)


async def _handle_file(hass, provider, requested, p, resolved_target, target_type):
    if provider.send_media is None:
        raise ValueError(f"Provider '{requested}' does not support media sending")
    resolved_type = _infer_media_type(p["file_path"], p["file_url"], p["media_type"])
    media_bytes, detected_name = await _read_media_source(hass, p["file_path"], p["file_url"])
    await provider.send_media(resolved_target, media_bytes, resolved_type, target_type, p["file_name"] or detected_name)
    if p["message"]:
        await provider.send_text(resolved_target, p["message"], target_type)


async def _handle_send_message(hass: HomeAssistant, call: ServiceCall) -> None:
    p = _extract_call_data(call)
    has_content = p["message"] or p["camera_entity"] or p["file_path"] or p["file_url"] or p["tts_text"] or p["card_json"] or p["card_content"] or p["card_buttons"]
    if not has_content:
        return

    requested, target_type = parse_channel(p["channel"])
    resolved_target = p["target"]
    providers = all_provider_runtimes(hass, requested)
    if not providers:
        _LOGGER.error("No matched provider runtime for send_message")
        return

    provider = (
        select_wechat_runtime(providers, wechat_account_id=p["wechat_account_id"], explicit_target=resolved_target)
        if requested == PROVIDER_WECHAT
        else select_provider_runtime(providers, explicit_target=resolved_target)
    )
    if provider is None:
        raise ValueError(f"Provider '{requested}' is ambiguous. Provide a target or ensure only one selector is active.")

    resolved_target = resolved_target or provider.selected_target()
    if not resolved_target:
        raise ValueError("target is required, or select a known target in the provider target selector entity")

    dispatch: list[tuple[bool, Any]] = [
        (bool(p["card_json"]), lambda: _handle_card(hass, provider, requested, p, resolved_target, target_type)),
        (bool(p["card_content"] or p["card_buttons"]), lambda: _handle_card_simple(hass, provider, requested, p, resolved_target, target_type)),
        (bool(p["approval_id"]), lambda: _dispatch_approval(provider, requested, p, resolved_target, target_type)),
        (bool(p["tts_text"]), lambda: _dispatch_tts(provider, requested, p, resolved_target, target_type)),
        (bool(p["camera_entity"]), lambda: _handle_camera(hass, provider, requested, p, resolved_target, target_type)),
        (bool(p["file_path"] or p["file_url"]), lambda: _handle_file(hass, provider, requested, p, resolved_target, target_type)),
    ]

    for condition, handler in dispatch:
        if condition:
            await handler()
            return

    if p["message_format"] and requested == "qq":
        sender = getattr(getattr(provider, "client", None), "send_text_formatted", None)
        if callable(sender):
            await sender(resolved_target, p["message"], target_type, p["message_format"])
            return
    await provider.send_text(resolved_target, p["message"], target_type)


async def _dispatch_approval(provider, requested, p, target, ttype):
    if provider.send_approval is None:
        raise ValueError(f"Provider '{requested}' does not support approval buttons")
    if not p["message"]:
        raise ValueError("message is required when approval_id is provided")
    await provider.send_approval(target, p["message"], ttype, p["approval_id"])


async def _dispatch_tts(provider, requested, p, target, ttype):
    if provider.send_tts is None:
        raise ValueError(f"Provider '{requested}' does not support TTS sending")
    await provider.send_tts(target, p["tts_text"], ttype)
    if p["message"]:
        await provider.send_text(target, p["message"], ttype)


# ── agent_mail 专用服务 ────────────────────────────────────────────────
_MAIL_SCHEMAS: dict[str, vol.Schema] = {
    SERVICE_LIST_MESSAGES: vol.Schema(
        {
            vol.Optional(ATTR_LIMIT, default=10): vol.Coerce(int),
            vol.Optional(ATTR_FOLDER, default="inbox"): vol.In(MAIL_FOLDERS),
            vol.Optional(ATTR_CURSOR, default=""): str,
        }
    ),
    SERVICE_READ_MESSAGE: vol.Schema({vol.Required(ATTR_MESSAGE_ID): str}),
    SERVICE_SEARCH_MESSAGES: vol.Schema(
        {
            vol.Required(ATTR_QUERY): str,
            vol.Optional(ATTR_SEARCH_IN, default="SEARCH_IN_ALL"): vol.In(MAIL_SEARCH_IN),
            vol.Optional(ATTR_LIMIT, default=10): vol.Coerce(int),
            vol.Optional(ATTR_CURSOR, default=""): str,
        }
    ),
    SERVICE_REPLY_MESSAGE: vol.Schema(
        {
            vol.Required(ATTR_MESSAGE_ID): str,
            vol.Required(ATTR_MESSAGE): str,
            vol.Optional(ATTR_REPLY_ALL, default=False): bool,
        }
    ),
    SERVICE_FORWARD_MESSAGE: vol.Schema(
        {
            vol.Required(ATTR_MESSAGE_ID): str,
            vol.Required("to"): str,
            vol.Optional(ATTR_INCLUDE_ATTACHMENTS, default=False): bool,
        }
    ),
    SERVICE_DELETE_MESSAGE: vol.Schema(
        {
            vol.Required(ATTR_MESSAGE_ID): str,
            vol.Optional(ATTR_PERMANENT, default=False): bool,
        }
    ),
}


async def _handle_mail_service(hass: HomeAssistant, call: ServiceCall) -> dict[str, Any]:
    providers = all_provider_runtimes(hass, PROVIDER_AGENT_MAIL)
    if not providers:
        raise ValueError("No agent_mail provider configured")
    provider = (
        providers[0]
        if len(providers) == 1
        else select_provider_runtime(providers, explicit_target=str(call.data.get(ATTR_TARGET, "")))
    )
    if provider is None:
        raise ValueError("agent_mail provider is ambiguous; provide a target")
    client = provider.client
    d = call.data
    svc = call.service

    if svc == SERVICE_LIST_MESSAGES:
        return await client.async_list_messages(
            limit=int(d.get(ATTR_LIMIT, 10) or 10),
            folder=str(d.get(ATTR_FOLDER, "inbox")),
            cursor=str(d.get(ATTR_CURSOR, "")),
        )
    if svc == SERVICE_READ_MESSAGE:
        return {"message": await client.async_read_message(str(d[ATTR_MESSAGE_ID]))}
    if svc == SERVICE_SEARCH_MESSAGES:
        return await client.async_search_messages(
            str(d[ATTR_QUERY]),
            str(d.get(ATTR_SEARCH_IN, "SEARCH_IN_ALL")),
            int(d.get(ATTR_LIMIT, 10) or 10),
            str(d.get(ATTR_CURSOR, "")),
        )
    if svc == SERVICE_REPLY_MESSAGE:
        await client.async_reply_message(
            str(d[ATTR_MESSAGE_ID]),
            str(d.get(ATTR_MESSAGE, "")),
            reply_all=bool(d.get(ATTR_REPLY_ALL, False)),
        )
        return {"queued": True}
    if svc == SERVICE_FORWARD_MESSAGE:
        to = [e.strip() for e in str(d.get("to", "")).split(",") if e.strip()]
        if not to:
            raise ValueError("forward_message: at least one recipient (to) is required")
        await client.async_forward_message(
            str(d[ATTR_MESSAGE_ID]), to, bool(d.get(ATTR_INCLUDE_ATTACHMENTS, False))
        )
        return {"queued": True}
    if svc == SERVICE_DELETE_MESSAGE:
        mid = str(d[ATTR_MESSAGE_ID])
        if bool(d.get(ATTR_PERMANENT, False)):
            await client.async_delete_message(mid)
        else:
            await client.async_trash_message(mid)
        return {"deleted": mid}
    raise ValueError(f"Unknown mail service: {svc}")


def register_services(hass: HomeAssistant) -> None:
    async def _service_handler(call: ServiceCall) -> None:
        await _handle_send_message(hass, call)

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_MESSAGE,
            _service_handler,
            schema=SERVICE_SCHEMA,
        )

    for name, schema in _MAIL_SCHEMAS.items():
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(
                DOMAIN,
                name,
                _handle_mail_service,
                schema=schema,
                supports_response=SupportsResponse.OPTIONAL,
            )
