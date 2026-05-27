"""Persistence for known inbound target identifiers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..const import DOMAIN

_STORE_VERSION = 1
_DATA_KEY = f"{DOMAIN}_known_targets"
_MAX_TARGETS = 20


@dataclass(slots=True)
class KnownTarget:
    provider: str
    target: str
    target_type: str
    display_name: str
    last_seen: str


class KnownTargetTracker:
    def __init__(self, hass: HomeAssistant, subentry_id: str) -> None:
        self._hass = hass
        self._subentry_id = subentry_id
        self._store: Store[list[dict[str, Any]]] = Store(hass, _STORE_VERSION, f"{DOMAIN}_targets_{subentry_id}")
        self._targets: list[KnownTarget] = []
        self._selected_target = ""

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        if isinstance(data, list):
            self._targets = [KnownTarget(**item) for item in data if isinstance(item, dict)]
            self._selected_target = self._targets[0].target if self._targets else ""
            return
        targets = data.get("targets") or []
        self._targets = [KnownTarget(**item) for item in targets if isinstance(item, dict)]
        self._selected_target = str(data.get("selected_target") or "")

    def snapshot(self) -> list[dict[str, str]]:
        return [asdict(item) for item in self._targets]

    def target_options(self) -> list[str]:
        return [item.target for item in self._targets]

    def selected_target(self) -> str:
        if self._selected_target and self._selected_target in self.target_options():
            return self._selected_target
        return self._targets[0].target if self._targets else ""

    async def async_select_target(self, target: str) -> None:
        self._selected_target = target.strip()
        await self._async_save()

    async def async_record(self, *, provider: str, target: str, target_type: str, display_name: str = "") -> None:
        target = target.strip()
        if not target:
            return
        updated = KnownTarget(
            provider=provider,
            target=target,
            target_type=target_type.strip(),
            display_name=display_name.strip(),
            last_seen=datetime.now(UTC).isoformat(),
        )
        remaining = [item for item in self._targets if not (item.target == updated.target and item.target_type == updated.target_type)]
        self._targets = [updated, *remaining][:_MAX_TARGETS]
        if not self._selected_target:
            self._selected_target = updated.target
        await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "targets": [asdict(item) for item in self._targets],
                "selected_target": self._selected_target,
            }
        )


async def async_get_tracker(hass: HomeAssistant, subentry_id: str) -> KnownTargetTracker:
    trackers: dict[str, KnownTargetTracker] = hass.data.setdefault(_DATA_KEY, {})
    tracker = trackers.get(subentry_id)
    if tracker is None:
        tracker = KnownTargetTracker(hass, subentry_id)
        await tracker.async_load()
        trackers[subentry_id] = tracker
    return tracker
