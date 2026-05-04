"""CN IM Hub integration."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import (
    ATTR_APPROVAL_ID,
    ATTR_CAMERA_ENTITY,
    ATTR_CHANNEL,
    ATTR_FILE_NAME,
    ATTR_FILE_PATH,
    ATTR_FILE_URL,
    ATTR_GIF_FPS,
    ATTR_LOOKBACK,
    ATTR_MESSAGE,
    ATTR_MESSAGE_FORMAT,
    ATTR_MEDIA_TYPE,
    ATTR_RECORD_DURATION,
    ATTR_TARGET,
    ATTR_TTS_TEXT,
    ATTR_WECHAT_ACCOUNT_ID,
    CHANNEL_DINGTALK_GROUP,
    CHANNEL_DINGTALK_USER,
    CHANNEL_FEISHU_CHAT_ID,
    CHANNEL_OPTIONS,
    CHANNEL_QQ_CHANNEL,
    CHANNEL_QQ_GROUP,
    CHANNEL_QQ_USER,
    CHANNEL_WECHAT_USER_ID,
    CHANNEL_WECOM_CHATID,
    CONF_AGENT_ID,
    DEFAULT_GIF_DURATION,
    DEFAULT_VIDEO_RECORD_DURATION,
    DOMAIN,
    PROVIDER_WECHAT,
    SERVICE_SEND_MESSAGE,
)
from .camera_media import (
    async_capture_camera_gif,
    async_record_camera_clip,
    async_resolve_camera_entity,
    resolve_ha_local_path,
)
from .models import HubRuntime, ProviderRuntime
from .providers.registry import get_provider_specs

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.SENSOR, Platform.SELECT]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SERVICE_SEND_MESSAGE_SCHEMA = vol.Schema(
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
    }
)


def _infer_media_type(file_path: str, file_url: str, explicit_media_type: str) -> str:
    if explicit_media_type:
        return explicit_media_type
    candidate = file_path or file_url
    suffix = Path(candidate.split("?", 1)[0]).suffix.lower() if candidate else ""
    if suffix in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
        return "image"
    if suffix in {".mp3", ".wav", ".silk", ".ogg", ".amr", ".m4a"}:
        return "voice"
    if suffix in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}:
        return "video"
    return "file"


async def _read_media_source(hass: HomeAssistant, file_path: str, file_url: str) -> tuple[bytes, str]:
    if file_path:
        path = resolve_ha_local_path(hass, file_path)
        if path is None:
            raise ValueError(f"file_path not found: {file_path}")
        data = await hass.async_add_executor_job(path.read_bytes)
        return data, path.name

    if file_url:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        async with session.get(file_url, timeout=60) as resp:
            if resp.status >= 400:
                raise ValueError(f"file_url download failed: {resp.status}")
            data = await resp.read()
        return data, Path(file_url.split("?", 1)[0]).name or "attachment.bin"

    raise ValueError("file_path or file_url is required")

async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    return True


def _normalize_stored_value(value: Any) -> Any:
    if isinstance(value, str):
        normalized = value.strip()
        lowered = normalized.lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
        return normalized
    if isinstance(value, dict):
        return {key: _normalize_stored_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_stored_value(item) for item in value]
    return value


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    options = dict(entry.options)
    agent_id = str(options.get(CONF_AGENT_ID, "")).strip()
    _LOGGER.debug("Setting up CN IM Hub entry %s with %d subentries", entry.entry_id, len(entry.subentries))

    runtimes = {}
    provider_specs = get_provider_specs()
    for subentry in entry.subentries.values():
        provider = subentry.subentry_type
        cfg = _normalize_stored_value(dict(subentry.data))
        spec = provider_specs.get(provider)
        if spec is None:
            _LOGGER.warning("Unknown provider in subentry: %s", provider)
            continue
        runtime = await spec.setup_provider(
            hass,
            cfg,
            agent_id=agent_id,
            subentry_id=subentry.subentry_id,
        )
        runtimes[f"{provider}:{subentry.subentry_id}"] = runtime

    entry.runtime_data = HubRuntime(providers=runtimes)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if runtimes and not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
        _register_services(hass)

    from .tmp_cleanup import async_setup_tmp_cleanup
    await async_setup_tmp_cleanup(hass)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    runtime: HubRuntime = entry.runtime_data
    for provider_runtime in runtime.providers.values():
        await provider_runtime.stop()

    has_any_provider = False
    for existing in hass.config_entries.async_entries(DOMAIN):
        if existing.entry_id == entry.entry_id:
            continue
        rt = getattr(existing, "runtime_data", None)
        if rt and getattr(rt, "providers", None):
            if rt.providers:
                has_any_provider = True
                break

    if not has_any_provider:
        if hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
            hass.services.async_remove(DOMAIN, SERVICE_SEND_MESSAGE)
        from .tmp_cleanup import async_unload_tmp_cleanup
        await async_unload_tmp_cleanup(hass)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _runtime_wechat_account_id(provider_runtime: ProviderRuntime) -> str:
    if provider_runtime.key != PROVIDER_WECHAT:
        return ""
    return str(getattr(provider_runtime.client, "_account_id", "")).strip()


def _matches_wechat_account(provider_runtime: ProviderRuntime, requested: str) -> bool:
    requested_value = requested.strip()
    if not requested_value:
        return False
    account_id = _runtime_wechat_account_id(provider_runtime)
    if requested_value == account_id:
        return True
    title = str(getattr(provider_runtime, "title", "")).strip()
    if requested_value == title:
        return True
    wrapped = f"WeChat ({account_id})" if account_id else ""
    return bool(wrapped and requested_value == wrapped)


def _all_provider_runtimes(hass: HomeAssistant, provider_key: str) -> list[ProviderRuntime]:
    providers: list[ProviderRuntime] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        runtime: HubRuntime = entry.runtime_data
        providers.extend(item for item in runtime.providers.values() if item.key == provider_key)
    return providers


def _select_wechat_runtime(
    runtimes: list[ProviderRuntime],
    *,
    wechat_account_id: str,
    explicit_target: str,
)-> ProviderRuntime | None:
    candidates = list(runtimes)
    if wechat_account_id:
        candidates = [item for item in candidates if _matches_wechat_account(item, wechat_account_id)]
        if len(candidates) == 1:
            return candidates[0]

    return _select_provider_runtime(candidates, explicit_target=explicit_target)


def _select_provider_runtime(
    runtimes: list[ProviderRuntime],
    *,
    explicit_target: str,
) -> ProviderRuntime | None:
    candidates = list(runtimes)
    if explicit_target:
        matched = [
            item
            for item in candidates
            if any(str(t.get("target", "")).strip() == explicit_target for t in item.known_targets())
        ]
        if len(matched) == 1:
            return matched[0]
        if matched:
            candidates = matched

    selected = [item for item in candidates if item.selected_target().strip()]
    if len(selected) == 1:
        return selected[0]

    return candidates[0] if len(candidates) == 1 else None


def _parse_channel(channel: str) -> tuple[str, str]:
    value = (channel or "").strip()
    mapping = {
        CHANNEL_FEISHU_CHAT_ID: ("feishu", "chat_id"),
        CHANNEL_WECOM_CHATID: ("wecom", "chatid"),
        CHANNEL_QQ_USER: ("qq", "user"),
        CHANNEL_QQ_GROUP: ("qq", "group"),
        CHANNEL_QQ_CHANNEL: ("qq", "channel"),
        CHANNEL_DINGTALK_USER: ("dingtalk", "user"),
        CHANNEL_DINGTALK_GROUP: ("dingtalk", "group"),
        CHANNEL_WECHAT_USER_ID: ("wechat", "user_id"),
    }
    mapped = mapping.get(value)
    if mapped is None:
        raise ValueError(f"Unsupported channel: {value}")
    return mapped


def _register_services(hass: HomeAssistant) -> None:
    async def _handle_send_message(call: ServiceCall) -> None:
        channel = str(call.data.get(ATTR_CHANNEL, CHANNEL_FEISHU_CHAT_ID))
        target = call.data.get(ATTR_TARGET, "")
        message = str(call.data.get(ATTR_MESSAGE, "")).strip()
        camera_entity = str(call.data.get(ATTR_CAMERA_ENTITY, "")).strip()
        file_path = str(call.data.get(ATTR_FILE_PATH, "")).strip()
        file_url = str(call.data.get(ATTR_FILE_URL, "")).strip()
        file_name = str(call.data.get(ATTR_FILE_NAME, "")).strip()
        media_type = str(call.data.get(ATTR_MEDIA_TYPE, "")).strip().lower()
        tts_text = str(call.data.get(ATTR_TTS_TEXT, "")).strip()
        message_format = str(call.data.get(ATTR_MESSAGE_FORMAT, "")).strip().lower()
        approval_id = str(call.data.get(ATTR_APPROVAL_ID, "")).strip()
        raw_record_duration = call.data.get(ATTR_RECORD_DURATION)
        record_duration = int(raw_record_duration) if raw_record_duration not in (None, "") else None
        lookback = int(call.data.get(ATTR_LOOKBACK, 0) or 0)
        gif_fps = int(call.data.get(ATTR_GIF_FPS, 2) or 2)
        wechat_account_id = str(call.data.get(ATTR_WECHAT_ACCOUNT_ID, "")).strip()
        if not message and not camera_entity and not file_path and not file_url and not tts_text:
            return
        requested, normalized_target_type = _parse_channel(channel)
        resolved_target = str(target or "").strip()

        providers = _all_provider_runtimes(hass, requested)
        if not providers:
            _LOGGER.error("No matched provider runtime for send_message")
            return

        if requested == PROVIDER_WECHAT:
            provider = _select_wechat_runtime(
                providers,
                wechat_account_id=wechat_account_id,
                explicit_target=resolved_target,
            )
            if provider is None:
                raise ValueError(
                    "WeChat account is ambiguous. Set wechat_account_id, or provide a target already seen by one account, "
                    "or ensure only one WeChat target selector currently has a selected target."
                )
        else:
            provider = _select_provider_runtime(providers, explicit_target=resolved_target)
            if provider is None:
                raise ValueError(
                    f"Provider '{requested}' is ambiguous. Provide a target already seen by one account, "
                    "or ensure only one target selector currently has a selected target."
                )

        if not resolved_target:
            resolved_target = provider.selected_target()
        if not resolved_target:
            raise ValueError("target is required, or select a known target in the provider target selector entity")
        if approval_id:
            if provider.send_approval is None:
                raise ValueError(f"Provider '{requested}' does not support approval buttons")
            if not message:
                raise ValueError("message is required when approval_id is provided")
            await provider.send_approval(resolved_target, message, normalized_target_type, approval_id)
            return
        if tts_text:
            if provider.send_tts is None:
                raise ValueError(f"Provider '{requested}' does not support TTS sending")
            await provider.send_tts(resolved_target, tts_text, normalized_target_type)
            if message:
                await provider.send_text(resolved_target, message, normalized_target_type)
            return
        if camera_entity:
            resolved_camera_entity = await async_resolve_camera_entity(hass, camera_entity)
            if resolved_camera_entity is None:
                raise ValueError(f"camera source not found: {camera_entity}")
            if media_type == "video":
                if provider.send_media is None:
                    raise ValueError(f"Provider '{requested}' does not support video sending")
                video_bytes, generated_name = await async_record_camera_clip(
                    hass,
                    resolved_camera_entity,
                    duration=record_duration or DEFAULT_VIDEO_RECORD_DURATION,
                    lookback=lookback,
                )
                await provider.send_media(
                    resolved_target,
                    video_bytes,
                    "video",
                    normalized_target_type,
                    file_name or generated_name,
                )
                if message:
                    await provider.send_text(resolved_target, message, normalized_target_type)
                return
            if media_type == "gif" or (media_type == "image" and file_name.lower().endswith(".gif")):
                if provider.send_image is None:
                    raise ValueError(f"Provider '{requested}' does not support GIF sending")
                gif_bytes, _ = await async_capture_camera_gif(
                    hass,
                    resolved_camera_entity,
                    duration=record_duration or DEFAULT_GIF_DURATION,
                    fps=gif_fps,
                )
                await provider.send_image(resolved_target, gif_bytes, normalized_target_type)
                if message:
                    await provider.send_text(resolved_target, message, normalized_target_type)
                return
            if provider.send_image is None:
                raise ValueError(f"Provider '{requested}' does not support camera image sending")
            from homeassistant.components.camera import async_get_image

            image = await async_get_image(hass, resolved_camera_entity)
            await provider.send_image(resolved_target, image.content, normalized_target_type)
            if message:
                await provider.send_text(resolved_target, message, normalized_target_type)
            return
        if file_path or file_url:
            if provider.send_media is None:
                raise ValueError(f"Provider '{requested}' does not support media sending")
            resolved_media_type = _infer_media_type(file_path, file_url, media_type)
            media_bytes, detected_name = await _read_media_source(hass, file_path, file_url)
            await provider.send_media(
                resolved_target,
                media_bytes,
                resolved_media_type,
                normalized_target_type,
                file_name or detected_name,
            )
            if message:
                await provider.send_text(resolved_target, message, normalized_target_type)
            return

        if message_format and requested == "qq":
            client = getattr(provider, "client", None)
            sender = getattr(client, "send_text_formatted", None)
            if callable(sender):
                await sender(resolved_target, message, normalized_target_type, message_format)
                return
        await provider.send_text(resolved_target, message, normalized_target_type)

    if not hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
        hass.services.async_register(
            DOMAIN,
            SERVICE_SEND_MESSAGE,
            _handle_send_message,
            schema=SERVICE_SEND_MESSAGE_SCHEMA,
        )
