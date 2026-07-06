from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from ..const import DOMAIN, PROVIDER_WECHAT
from ..models import HubRuntime, ProviderRuntime

CHANNEL_MAP: dict[str, tuple[str, str]] = {
    "feishu/chat_id": ("feishu", "chat_id"),
    "feishu/open_id": ("feishu", "open_id"),
    "wecom/chatid": ("wecom", "chatid"),
    "qq/user": ("qq", "user"),
    "qq/group": ("qq", "group"),
    "qq/channel": ("qq", "channel"),
    "dingtalk/user": ("dingtalk", "user"),
    "dingtalk/group": ("dingtalk", "group"),
    "wechat/user_id": ("wechat", "user_id"),
}


def parse_channel(channel: str) -> tuple[str, str]:
    result = CHANNEL_MAP.get((channel or "").strip())
    if result is None:
        raise ValueError(f"Unsupported channel: {channel}")
    return result


def all_provider_runtimes(hass: HomeAssistant, provider_key: str) -> list[ProviderRuntime]:
    return [
        item
        for entry in hass.config_entries.async_entries(DOMAIN)
        for item in getattr(entry, "runtime_data", HubRuntime({})).providers.values()
        if item.key == provider_key
    ]


def _runtime_wechat_account_id(rt: ProviderRuntime) -> str:
    return str(getattr(rt.client, "_account_id", "")).strip() if rt.key == PROVIDER_WECHAT else ""


def _matches_wechat_account(rt: ProviderRuntime, requested: str) -> bool:
    requested_value = requested.strip()
    if not requested_value:
        return False
    account_id = _runtime_wechat_account_id(rt)
    return requested_value in (
        account_id,
        str(getattr(rt, "title", "")).strip(),
        f"WeChat ({account_id})" if account_id else "",
    )


def select_provider_runtime(
    runtimes: list[ProviderRuntime],
    *,
    explicit_target: str,
) -> ProviderRuntime | None:
    candidates = list(runtimes)
    if explicit_target:
        matched = [
            r for r in candidates
            if any(str(t.get("target", "")).strip() == explicit_target for t in r.known_targets())
        ]
        candidates = matched if matched else candidates
        if len(matched) == 1:
            return matched[0]

    selected = [r for r in candidates if r.selected_target().strip()]
    return selected[0] if len(selected) == 1 else (candidates[0] if len(candidates) == 1 else None)


def select_wechat_runtime(
    runtimes: list[ProviderRuntime],
    *,
    wechat_account_id: str,
    explicit_target: str,
) -> ProviderRuntime | None:
    candidates = (
        [r for r in runtimes if _matches_wechat_account(r, wechat_account_id)]
        if wechat_account_id else list(runtimes)
    )
    return candidates[0] if wechat_account_id and len(candidates) == 1 else select_provider_runtime(candidates, explicit_target=explicit_target)
