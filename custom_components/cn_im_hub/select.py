from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .models import ProviderRuntime

_DEVICE_MODEL = "IM Channel Gateway"
_MANUFACTURER = "Home Assistant China (unofficial)"

_CHANNEL_ICONS: dict[str, str] = {
    "qq": "mdi:qqchat",
    "wechat": "mdi:wechat",
    "feishu": "mdi:bird",
    "wecom": "mdi:briefcase-account",
    "dingtalk": "mdi:bell-ring",
    "xiaoyi": "mdi:robot",
}
_DEFAULT_ICON = "mdi:message-text"


def _device(entry: ConfigEntry, runtime_key: str, title: str) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id, runtime_key)},
        name=title,
        manufacturer=_MANUFACTURER,
        model=_DEVICE_MODEL,
        entry_type="service",
    )


def _provider(entry: ConfigEntry, key: str) -> ProviderRuntime | None:
    return entry.runtime_data.providers.get(key)


def _cleanup_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    registry = er.async_get(hass)
    valid = {f"{entry.entry_id}_{rk}_active_target" for rk in entry.runtime_data.providers}
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.domain == "select" and ent.platform == DOMAIN and ent.unique_id not in valid:
            registry.async_remove(ent.entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    _cleanup_stale_entities(hass, entry)
    for rk, rt in entry.runtime_data.providers.items():
        async_add_entities(
            [ChannelActiveTargetSelect(entry, rk, rt.key, rt.title)],
            True,
            config_subentry_id=rt.subentry_id,
        )


class ChannelActiveTargetSelect(SelectEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime_key: str, provider_key: str, title: str) -> None:
        self._entry = entry
        self._runtime_key = runtime_key
        self._title = title
        self._attr_unique_id = f"{entry.entry_id}_{runtime_key}_active_target"
        self._attr_name = "Active Target"
        self._attr_icon = _CHANNEL_ICONS.get(provider_key, _DEFAULT_ICON)

    @property
    def current_option(self) -> str | None:
        p = _provider(self._entry, self._runtime_key)
        if p is None:
            return None
        selected = p.selected_target()
        if selected:
            return selected
        return "waiting" if not p.known_targets() else None

    @property
    def options(self) -> list[str]:
        p = _provider(self._entry, self._runtime_key)
        if p is None:
            return []
        targets = [t.get("target", "") for t in p.known_targets() if t.get("target")]
        if not targets:
            return ["waiting"]
        return targets

    async def async_select_option(self, option: str) -> None:
        p = _provider(self._entry, self._runtime_key)
        if p is None:
            return
        await p.select_target(option)
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        p = _provider(self._entry, self._runtime_key)
        return {} if p is None else {
            "channel": p.key,
            "capabilities": p.capabilities,
            "capability_tier": p.capability_tier,
        }

    @property
    def device_info(self) -> DeviceInfo:
        return _device(self._entry, self._runtime_key, self._title)
