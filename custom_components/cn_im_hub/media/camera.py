"""Camera media helpers for CN IM Hub."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
import shutil
import tempfile
import time

from homeassistant.core import HomeAssistant


def resolve_ha_local_path(hass: HomeAssistant, source: str) -> Path | None:
    """Resolve Home Assistant virtual/local paths to a real filesystem path."""

    candidate = source.strip()
    if not candidate:
        return None

    direct_path = Path(candidate)
    if direct_path.is_file():
        return direct_path

    if candidate.startswith("/config/"):
        resolved = Path(hass.config.path(candidate.removeprefix("/config/")))
        return resolved if resolved.is_file() else None

    if candidate.startswith("/local/"):
        relative = candidate.removeprefix("/local/").lstrip("/")
        resolved = Path(hass.config.path("www", relative))
        return resolved if resolved.is_file() else None

    if candidate.startswith("/media/local/"):
        relative = candidate.removeprefix("/media/local/").lstrip("/")
        resolved = Path(hass.config.path("media", relative))
        return resolved if resolved.is_file() else None

    return None


async def async_resolve_camera_entity(
    hass: HomeAssistant,
    source: str,
) -> str | None:
    """Resolve a camera source to a concrete entity_id.

    Accepts full entity IDs like ``camera.front_door`` and shorthand object IDs
    like ``front_door``.
    """

    candidate = source.strip()
    if not candidate:
        return None

    if candidate.startswith("camera."):
        state = hass.states.get(candidate)
        if state is not None:
            return candidate

    state = hass.states.get(candidate)
    if state is not None and state.entity_id.startswith("camera."):
        return state.entity_id

    prefixed = f"camera.{candidate}"
    if hass.states.get(prefixed) is not None:
        return prefixed

    lowered = candidate.casefold().removeprefix("camera.").replace("_", "").replace("-", "")
    for camera_state in hass.states.async_all("camera"):
        entity_id = camera_state.entity_id
        normalized = entity_id.casefold().removeprefix("camera.").replace("_", "").replace("-", "")
        if normalized == lowered:
            return entity_id
        friendly_name = str(camera_state.attributes.get("friendly_name") or "").strip()
        if friendly_name and friendly_name.casefold().replace("_", "").replace("-", "") == lowered:
            return entity_id

    return None


async def async_record_camera_clip(
    hass: HomeAssistant,
    camera_entity: str,
    *,
    duration: int = 8,
    lookback: int = 0,
) -> tuple[bytes, str]:
    """Record a short camera clip via Home Assistant camera.record."""

    resolved_camera = await async_resolve_camera_entity(hass, camera_entity)
    if resolved_camera is None:
        raise ValueError(f"Camera source not found: {camera_entity}")

    with tempfile.NamedTemporaryFile(prefix="cn_im_hub_camera_", suffix=".mp4", delete=False) as tmp:
        output_path = Path(tmp.name)
    compat_path = output_path

    try:
        await hass.services.async_call(
            "camera",
            "record",
            {
                "entity_id": resolved_camera,
                "filename": str(output_path),
                "duration": max(1, int(duration)),
                "lookback": max(0, int(lookback)),
            },
            blocking=True,
        )
        await _async_wait_for_file(output_path, timeout=max(15, duration + lookback + 10))
        compat_path = await _async_ensure_mp4_compatible(
            output_path,
            timeout=max(30, duration + lookback + 20),
        )
        data = await hass.async_add_executor_job(compat_path.read_bytes)
        return data, f"{resolved_camera.replace('.', '_')}_{int(time.time())}.mp4"
    finally:
        output_path.unlink(missing_ok=True)
        if compat_path != output_path:
            compat_path.unlink(missing_ok=True)


async def async_capture_camera_gif(
    hass: HomeAssistant,
    camera_entity: str,
    *,
    duration: int = 3,
    fps: int = 2,
    max_dim: int = 960,
) -> tuple[bytes, str]:
    """Capture a short animated GIF from camera snapshots."""

    from homeassistant.components.camera import async_get_image

    resolved_camera = await async_resolve_camera_entity(hass, camera_entity)
    if resolved_camera is None:
        raise ValueError(f"Camera source not found: {camera_entity}")

    total_frames = max(2, int(duration) * max(1, int(fps)))
    interval = max(0.15, 1 / max(1, int(fps)))
    frames: list[bytes] = []

    for index in range(total_frames):
        image = await async_get_image(hass, resolved_camera)
        frames.append(image.content)
        if index < total_frames - 1:
            await asyncio.sleep(interval)

    gif_bytes = await hass.async_add_executor_job(
        _build_gif,
        frames,
        max_dim,
        int(1000 / max(1, int(fps))),
    )
    return gif_bytes, f"{resolved_camera.replace('.', '_')}_{int(time.time())}.gif"


async def async_record_remote_stream_clip(
    hass: HomeAssistant,
    stream_url: str,
    *,
    duration: int = 8,
) -> tuple[bytes, str]:
    """Record a short MP4 clip from a remote stream URL via ffmpeg."""

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("ffmpeg binary not found")

    with tempfile.NamedTemporaryFile(prefix="cn_im_hub_remote_", suffix=".mp4", delete=False) as tmp:
        output_path = Path(tmp.name)

    try:
        cmd = [
            ffmpeg_bin,
            "-y",
            "-t",
            str(max(1, int(duration))),
            "-i",
            stream_url,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-movflags",
            "+faststart",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "baseline",
            "-level",
            "3.1",
            "-c:a",
            "aac",
            str(output_path),
        ]
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
            stderr_text = (stderr or b"").decode("utf-8", errors="replace")
            fallback_cmd = [
                ffmpeg_bin,
                "-y",
                "-t",
                str(max(1, int(duration))),
                "-i",
                stream_url,
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                str(output_path),
            ]
            process = await asyncio.create_subprocess_exec(
                *fallback_cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr2 = await process.communicate()
            if process.returncode != 0 or not output_path.is_file() or output_path.stat().st_size <= 0:
                raise RuntimeError(
                    "ffmpeg remote stream recording failed: "
                    + ((stderr2 or stderr or b"").decode("utf-8", errors="replace")[:500] or "unknown")
                )
        data = await hass.async_add_executor_job(output_path.read_bytes)
        return data, f"remote_stream_{int(time.time())}.mp4"
    finally:
        output_path.unlink(missing_ok=True)


async def _async_wait_for_file(path: Path, *, timeout: int) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return
        await asyncio.sleep(0.5)
    raise TimeoutError(f"Timed out waiting for media file: {path}")


async def _async_ensure_mp4_compatible(path: Path, *, timeout: int) -> Path:
    """Remux/transcode a recorded clip into a widely compatible MP4 when possible."""

    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return path

    with tempfile.NamedTemporaryFile(prefix="cn_im_hub_camera_compat_", suffix=".mp4", delete=False) as tmp:
        compat_path = Path(tmp.name)

    commands = [
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "baseline",
            "-level",
            "3.1",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(compat_path),
        ],
        [
            ffmpeg_bin,
            "-y",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(compat_path),
        ],
    ]

    for command in commands:
        compat_path.unlink(missing_ok=True)
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.communicate()
            continue
        if process.returncode == 0 and compat_path.is_file() and compat_path.stat().st_size > 0:
            return compat_path
        _ = stderr

    compat_path.unlink(missing_ok=True)
    return path


def _build_gif(frames: list[bytes], max_dim: int, duration_ms: int) -> bytes:
    from PIL import Image

    def _prepare_frames(target_dim: int) -> list[Image.Image]:
        prepared: list[Image.Image] = []
        for raw in frames:
            image = Image.open(BytesIO(raw))
            if image.mode not in ("RGB", "P"):
                image = image.convert("RGB")
            else:
                image = image.convert("RGB")
            width, height = image.size
            if max(width, height) > target_dim:
                scale = target_dim / max(width, height)
                image = image.resize((max(1, int(width * scale)), max(1, int(height * scale))), Image.LANCZOS)
            image = image.quantize(colors=128, method=Image.MEDIANCUT)
            prepared.append(image.copy())
            image.close()
        return prepared

    def _encode(prepared_frames: list[Image.Image]) -> bytes:
        if not prepared_frames:
            raise ValueError("No frames captured for GIF")
        out = BytesIO()
        prepared_frames[0].save(
            out,
            format="GIF",
            save_all=True,
            append_images=prepared_frames[1:],
            duration=duration_ms,
            loop=0,
            optimize=True,
            disposal=2,
        )
        return out.getvalue()

    primary_frames = _prepare_frames(max_dim)
    gif_bytes = _encode(primary_frames)
    if len(gif_bytes) <= 2 * 1024 * 1024:
        return gif_bytes

    fallback_dim = max(240, int(max_dim * 0.75))
    fallback_frames = _prepare_frames(fallback_dim)
    return _encode(fallback_frames)
