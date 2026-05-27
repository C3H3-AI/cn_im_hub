"""QQ chunked upload helpers."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
import tempfile
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant

_MD5_10M_SIZE = 10_002_432
_PART_UPLOAD_TIMEOUT_SECONDS = 300
_MAX_CONCURRENT_PARTS = 10


async def async_upload_media_chunked(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    *,
    token: str,
    api_base: str,
    ident: str,
    kind: str,
    file_type: int,
    file_bytes: bytes,
    file_name: str,
) -> str:
    """Upload QQ media via upload_prepare/upload_part_finish flow."""

    with tempfile.NamedTemporaryFile(prefix="cn_im_hub_qq_", suffix=Path(file_name).suffix or ".bin", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_path = Path(tmp.name)

    try:
        return await _async_upload_media_chunked_path(
            hass,
            session,
            token=token,
            api_base=api_base,
            ident=ident,
            kind=kind,
            file_type=file_type,
            file_path=tmp_path,
            file_name=file_name,
        )
    finally:
        tmp_path.unlink(missing_ok=True)


async def _async_upload_media_chunked_path(
    hass: HomeAssistant,
    session: aiohttp.ClientSession,
    *,
    token: str,
    api_base: str,
    ident: str,
    kind: str,
    file_type: int,
    file_path: Path,
    file_name: str,
) -> str:
    file_size = file_path.stat().st_size
    hashes = await hass.async_add_executor_job(_compute_file_hashes, file_path)
    prepare = await _async_upload_prepare(
        session,
        token=token,
        api_base=api_base,
        ident=ident,
        kind=kind,
        file_type=file_type,
        file_name=file_name,
        file_size=file_size,
        hashes=hashes,
    )

    upload_id = str(prepare.get("upload_id") or "")
    parts = prepare.get("parts") or []
    block_size = int(prepare.get("block_size") or 0)
    if not upload_id or not isinstance(parts, list) or not parts or block_size <= 0:
        raise RuntimeError(f"QQ upload_prepare invalid response: {prepare}")

    concurrency = min(max(int(prepare.get("concurrency") or 1), 1), _MAX_CONCURRENT_PARTS)
    retry_timeout_ms = int(prepare.get("retry_timeout") or 0) * 1000 or None

    semaphore = asyncio.Semaphore(concurrency)

    async def _upload_one(part: dict[str, Any]) -> None:
        async with semaphore:
            part_index = int(part.get("index") or 0)
            presigned_url = str(part.get("presigned_url") or "")
            if part_index <= 0 or not presigned_url:
                raise RuntimeError(f"QQ upload_prepare invalid part: {part}")
            offset = (part_index - 1) * block_size
            length = min(block_size, file_size - offset)
            chunk = await hass.async_add_executor_job(_read_file_chunk, file_path, offset, length)
            md5_hex = hashlib.md5(chunk, usedforsecurity=False).hexdigest()
            await _async_put_presigned_url(session, presigned_url, chunk)
            await _async_upload_part_finish(
                session,
                token=token,
                api_base=api_base,
                ident=ident,
                kind=kind,
                upload_id=upload_id,
                part_index=part_index,
                block_size=length,
                md5_hex=md5_hex,
                retry_timeout_ms=retry_timeout_ms,
            )

    await asyncio.gather(*[_upload_one(part) for part in parts])
    completed = await _async_complete_upload(
        session,
        token=token,
        api_base=api_base,
        ident=ident,
        kind=kind,
        upload_id=upload_id,
    )
    file_info = str(completed.get("file_info") or "")
    if not file_info:
        raise RuntimeError(f"QQ complete upload missing file_info: {completed}")
    return file_info


def _compute_file_hashes(file_path: Path) -> dict[str, str]:
    md5_hash = hashlib.md5(usedforsecurity=False)
    sha1_hash = hashlib.sha1(usedforsecurity=False)
    md5_10m_hash = hashlib.md5(usedforsecurity=False)
    total_read = 0
    with file_path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            md5_hash.update(chunk)
            sha1_hash.update(chunk)
            remaining = _MD5_10M_SIZE - total_read
            if remaining > 0:
                md5_10m_hash.update(chunk[:remaining])
            total_read += len(chunk)
    md5_hex = md5_hash.hexdigest()
    return {
        "md5": md5_hex,
        "sha1": sha1_hash.hexdigest(),
        "md5_10m": md5_10m_hash.hexdigest() if total_read > _MD5_10M_SIZE else md5_hex,
    }


def _read_file_chunk(file_path: Path, offset: int, length: int) -> bytes:
    with file_path.open("rb") as handle:
        handle.seek(offset)
        return handle.read(length)


def _upload_prepare_path(api_base: str, ident: str, kind: str) -> str:
    if kind == "user":
        return f"{api_base}/v2/users/{ident}/upload_prepare"
    if kind == "group":
        return f"{api_base}/v2/groups/{ident}/upload_prepare"
    raise ValueError("QQ chunked upload only supports user and group targets")


def _upload_part_finish_path(api_base: str, ident: str, kind: str) -> str:
    if kind == "user":
        return f"{api_base}/v2/users/{ident}/upload_part_finish"
    if kind == "group":
        return f"{api_base}/v2/groups/{ident}/upload_part_finish"
    raise ValueError("QQ chunked upload only supports user and group targets")


def _complete_upload_path(api_base: str, ident: str, kind: str) -> str:
    if kind == "user":
        return f"{api_base}/v2/users/{ident}/files"
    if kind == "group":
        return f"{api_base}/v2/groups/{ident}/files"
    raise ValueError("QQ chunked upload only supports user and group targets")


async def _async_upload_prepare(
    session: aiohttp.ClientSession,
    *,
    token: str,
    api_base: str,
    ident: str,
    kind: str,
    file_type: int,
    file_name: str,
    file_size: int,
    hashes: dict[str, str],
) -> dict[str, Any]:
    body = {
        "file_type": file_type,
        "file_name": file_name,
        "file_size": file_size,
        "md5": hashes["md5"],
        "sha1": hashes["sha1"],
        "md5_10m": hashes["md5_10m"],
    }
    async with session.post(
        _upload_prepare_path(api_base, ident, kind),
        headers={"Authorization": f"QQBot {token}"},
        json=body,
        timeout=60,
    ) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 400:
            raise RuntimeError(f"QQ upload_prepare failed: {resp.status} {data}")
    return data


async def _async_put_presigned_url(
    session: aiohttp.ClientSession,
    presigned_url: str,
    chunk: bytes,
) -> None:
    timeout = aiohttp.ClientTimeout(total=_PART_UPLOAD_TIMEOUT_SECONDS)
    async with session.put(
        presigned_url,
        data=chunk,
        headers={"Content-Length": str(len(chunk))},
        timeout=timeout,
    ) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"QQ presigned PUT failed: {resp.status} {await resp.text()}")


async def _async_upload_part_finish(
    session: aiohttp.ClientSession,
    *,
    token: str,
    api_base: str,
    ident: str,
    kind: str,
    upload_id: str,
    part_index: int,
    block_size: int,
    md5_hex: str,
    retry_timeout_ms: int | None,
) -> None:
    body = {
        "upload_id": upload_id,
        "part_index": part_index,
        "block_size": block_size,
        "md5": md5_hex,
    }
    timeout = aiohttp.ClientTimeout(total=(retry_timeout_ms / 1000) if retry_timeout_ms else 60)
    async with session.post(
        _upload_part_finish_path(api_base, ident, kind),
        headers={"Authorization": f"QQBot {token}"},
        json=body,
        timeout=timeout,
    ) as resp:
        if resp.status >= 400:
            raise RuntimeError(f"QQ upload_part_finish failed: {resp.status} {await resp.text()}")


async def _async_complete_upload(
    session: aiohttp.ClientSession,
    *,
    token: str,
    api_base: str,
    ident: str,
    kind: str,
    upload_id: str,
) -> dict[str, Any]:
    async with session.post(
        _complete_upload_path(api_base, ident, kind),
        headers={"Authorization": f"QQBot {token}"},
        json={"upload_id": upload_id},
        timeout=60,
    ) as resp:
        data = await resp.json(content_type=None)
        if resp.status >= 400:
            raise RuntimeError(f"QQ complete upload failed: {resp.status} {data}")
    return data
