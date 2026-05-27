from __future__ import annotations

import logging
import time
from datetime import timedelta
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

_LOGGER = logging.getLogger(__name__)

TMP_RETENTION_HOURS = 6
_CLEAN_INTERVAL = timedelta(hours=1)
_DATA_KEY = "cn_im_hub_tmp_cleanup_unsub"


def _get_tmp_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(".storage", "cn_im_hub", "tmp"))


def _sweep(tmp_dir: Path, retention_seconds: float) -> tuple[int, int]:
    if not tmp_dir.is_dir():
        return 0, 0

    cutoff = time.time() - retention_seconds
    removed_files = 0
    removed_bytes = 0

    for path in sorted(tmp_dir.rglob("*"), reverse=True):
        try:
            if path.is_file():
                stat = path.stat()
                if stat.st_mtime < cutoff:
                    removed_bytes += stat.st_size
                    path.unlink()
                    removed_files += 1
            elif path.is_dir():
                try:
                    next(path.iterdir())
                except StopIteration:
                    if path != tmp_dir:
                        path.rmdir()
        except OSError as err:
            _LOGGER.debug("tmp cleanup skipped %s: %s", path, err)

    return removed_files, removed_bytes


async def async_setup_tmp_cleanup(hass: HomeAssistant) -> None:
    if _DATA_KEY in hass.data:
        return

    tmp_dir = _get_tmp_dir(hass)
    retention_seconds = float(TMP_RETENTION_HOURS) * 3600.0

    async def _tick(_now) -> None:
        files, total_bytes = await hass.async_add_executor_job(
            _sweep, tmp_dir, retention_seconds
        )
        if files:
            _LOGGER.info(
                "Pruned %d tmp file(s) (%d bytes) older than %dh",
                files, total_bytes, TMP_RETENTION_HOURS,
            )

    await hass.async_add_executor_job(_sweep, tmp_dir, retention_seconds)

    unsub = async_track_time_interval(hass, _tick, _CLEAN_INTERVAL)
    hass.data[_DATA_KEY] = unsub


async def async_unload_tmp_cleanup(hass: HomeAssistant) -> None:
    unsub = hass.data.pop(_DATA_KEY, None)
    if unsub is not None:
        try:
            unsub()
        except Exception as err:
            _LOGGER.debug("tmp_cleanup unload error: %s", err)
