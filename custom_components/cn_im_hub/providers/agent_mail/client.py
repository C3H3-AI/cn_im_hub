"""Tencent Agently Mail (agent.qq.com) provider for cn_im_hub.

Protocol source: reverse-engineered @tencent-qqmail/agently-cli v1.0.15
(MITM + --dry-run, 2026-08-18) cross-verified against Bahtya/agently-mail
(live-verified Cloudflare Worker, 2026-07-07). See
D:/ai-hub/reverse-engineering/agently-mail/API_CONTRACT.md
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import (
    CONF_AGENT_MAIL_ACCESS_TOKEN,
    CONF_AGENT_MAIL_ALIAS_ID,
    CONF_AGENT_MAIL_REFRESH_TOKEN,
    DOMAIN,
    PROVIDER_AGENT_MAIL,
)
from ...models import ProviderRuntime
from ..base import ProviderSpec
from .flow import AgentMailProviderSubentryFlow

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://api.agent.qq.com"
AUTH_BASE = "https://auth.agent.qq.com"
CLIENT_ID = "cli_002e8cd1f5e97858"  # v1.0.15 公开常量（版本相关，非秘密）
CLIENT_VERSION = "1.0.15"
# 服务器按 User-Agent 校验客户端身份，必须用腾讯认可的 CLI 标识（实测 v1.0.15 组合）
UA = "agently-cli/1.0.15 (windows/amd64; agent/workbuddy)"

DEFAULT_SUBJECT = "Home Assistant"
MAX_CONFIRM_RETRY = 2


class AgentMailError(Exception):
    """Base error for Agent Mail."""


class ConfirmationRequired(AgentMailError):
    """Two-phase send: server asked for explicit confirmation."""

    def __init__(self, confirmation_token: str, summary: str, expires_in: int | None = None) -> None:
        super().__init__("confirmation required")
        self.confirmation_token = confirmation_token
        self.summary = summary
        self.expires_in = expires_in


class AgentMailClient:
    """Async REST client for agent.qq.com (OAuth device flow / Bearer auth)."""

    def __init__(
        self,
        hass: HomeAssistant,
        access_token: str,
        refresh_token: str = "",
        alias_id: str = "",
        subentry_id: str = "",
        on_tokens_refreshed=None,
    ) -> None:
        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._alias_id = alias_id
        self._subentry_id = subentry_id
        self._on_tokens_refreshed = on_tokens_refreshed
        self._email = ""
        self._name = ""
        self._known_contacts: list[dict[str, str]] = []
        self._selected_target = ""
        self._status = "initializing"
        self._lock = asyncio.Lock()

    # ── auth ──────────────────────────────────────────────────────────
    async def _refresh(self) -> bool:
        """Refresh access token via form-encoded /oauth/token. Rotates refresh_token."""
        form = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": CLIENT_ID,
            "clientversion": CLIENT_VERSION,
        }
        try:
            async with self._session.post(
                f"{AUTH_BASE}/oauth/token", data=form, headers={"User-Agent": UA}
            ) as resp:
                if resp.status >= 400:
                    _LOGGER.warning("agent_mail refresh failed: HTTP %s", resp.status)
                    return False
                j = await resp.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("agent_mail refresh error: %s", err)
            return False
        if not isinstance(j, dict) or not j.get("access_token"):
            return False
        self._access_token = j["access_token"]
        if j.get("refresh_token"):
            self._refresh_token = j["refresh_token"]  # rotation
        if callable(self._on_tokens_refreshed):
            try:
                await self._on_tokens_refreshed(self._access_token, self._refresh_token)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning("agent_mail token persistence failed: %s", err)
        return True

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
        """One /v1 call with Bearer auth; on 401 refresh once and retry."""
        url = f"{API_BASE}{path}"
        headers = {"User-Agent": UA, "Accept": "application/json"}
        data: dict[str, Any] | None = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            data = payload

        async def _go(token: str) -> tuple[int, Any]:
            h = {**headers, "Authorization": f"Bearer {token}"}
            async with self._session.request(method, url, headers=h, json=data) as resp:
                body = await resp.text()
                try:
                    j = await resp.json()
                except Exception:  # noqa: BLE001
                    j = body
                return resp.status, j

        status, j = await _go(self._access_token)
        if status == 401 and self._refresh_token:
            async with self._lock:
                if await self._refresh():
                    status, j = await _go(self._access_token)
        return status, j

    async def _require_alias(self) -> str:
        if not self._alias_id:
            await self.async_get_me()
        if not self._alias_id:
            raise AgentMailError("alias_id not resolved from /v1/me")
        return self._alias_id

    # ── account ───────────────────────────────────────────────────────
    async def async_get_me(self) -> dict[str, Any]:
        status, j = await self._request("GET", "/v1/me")
        if status >= 400:
            raise AgentMailError(f"/v1/me failed: HTTP {status} ({j})")
        data = j.get("data") or {}
        aliases = data.get("aliases") or []
        if aliases:
            primary = next((a for a in aliases if a.get("is_primary")), aliases[0])
            self._alias_id = self._alias_id or primary.get("alias_id", "")
            self._email = primary.get("email", "")
            self._name = primary.get("name", "")
            self._selected_target = self._selected_target or self._email
        self._status = "connected"
        return data

    async def start(self) -> None:
        await self.async_get_me()

    async def stop(self) -> None:
        self._status = "disconnected"

    # ── messages ──────────────────────────────────────────────────────
    async def async_list_messages(
        self, limit: int = 10, folder: str = "inbox", cursor: str = ""
    ) -> dict[str, Any]:
        aid = await self._require_alias()
        params = [f"limit={int(limit)}", f"dir={folder}"]
        if cursor:
            params.append(f"cursor={cursor}")
        status, j = await self._request("GET", f"/v1/aliases/{aid}/messages?{'&'.join(params)}")
        if status >= 400:
            raise AgentMailError(f"list messages failed: HTTP {status} ({j})")
        jj = j if isinstance(j, dict) else {}
        data = jj.get("data") or []
        messages = data if isinstance(data, list) else []
        # 顺带积累发件人作为可发送目标（select 目标选择器数据源）
        for m in messages[:20]:
            self._record_contact(m.get("from"))
        return {"messages": messages, "pagination": jj.get("pagination") or {}}

    async def async_read_message(self, message_id: str) -> dict[str, Any]:
        aid = await self._require_alias()
        status, j = await self._request("GET", f"/v1/aliases/{aid}/messages/{message_id}")
        if status >= 400:
            raise AgentMailError(f"read message failed: HTTP {status} ({j})")
        data = j.get("data") if isinstance(j, dict) else j
        if isinstance(data, dict):
            self._record_contact(data.get("from"))
        return data or {}

    async def async_search_messages(
        self, query: str, search_in: str = "SEARCH_IN_ALL", limit: int = 10, cursor: str = ""
    ) -> dict[str, Any]:
        aid = await self._require_alias()
        params = [f"q={query}", f"search_in={search_in}", f"limit={int(limit)}"]
        if cursor:
            params.append(f"cursor={cursor}")
        status, j = await self._request("GET", f"/v1/aliases/{aid}/messages/search?{'&'.join(params)}")
        if status >= 400:
            raise AgentMailError(f"search failed: HTTP {status} ({j})")
        jj = j if isinstance(j, dict) else {}
        data = jj.get("data") or []
        return {"messages": data if isinstance(data, list) else [], "pagination": jj.get("pagination") or {}}

    async def async_send_message(
        self,
        to: list[str],
        subject: str = DEFAULT_SUBJECT,
        body: str = "",
        body_format: str = "PLAIN",
        cc: list[str] | None = None,
        bcc: list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Send with automatic two-phase confirmation (HA call = explicit user action)."""
        aid = await self._require_alias()
        payload: dict[str, Any] = {
            "to": [{"email": e} for e in to],
            "cc": [{"email": e} for e in (cc or [])],
            "bcc": [{"email": e} for e in (bcc or [])],
            "subject": subject,
            "body": body,
            "body_format": body_format.upper(),
        }
        if attachments:
            payload["attachments"] = attachments

        status, j = await self._request("POST", f"/v1/aliases/{aid}/messages/send", payload)
        # phase 1 → confirmation required
        err = j.get("error") if isinstance(j, dict) else None
        if err and err.get("code") == "CONFIRMATION_REQUIRED":
            details = err.get("details") or {}
            token = details.get("confirmation_token") or details.get("confirmationToken")
            if not token:
                raise AgentMailError("send requires confirmation but no token returned")
            confirm = {**payload, "confirmation_token": token}
            status2, j2 = await self._request("POST", f"/v1/aliases/{aid}/messages/send", confirm)
            if status2 >= 400:
                raise AgentMailError(f"send confirm failed: HTTP {status2} ({j2})")
            return j2 if isinstance(j2, dict) else {"queued": True}
        if status >= 400:
            raise AgentMailError(f"send failed: HTTP {status} ({j})")
        return j if isinstance(j, dict) else {"queued": True}

    async def async_reply_message(
        self, message_id: str, body: str, body_format: str = "PLAIN", reply_all: bool = False
    ) -> dict[str, Any]:
        aid = await self._require_alias()
        payload = {"body": body, "body_format": body_format.upper(), "reply_all": reply_all}
        status, j = await self._request("POST", f"/v1/aliases/{aid}/messages/{message_id}/reply", payload)
        err = j.get("error") if isinstance(j, dict) else None
        if err and err.get("code") == "CONFIRMATION_REQUIRED":
            details = err.get("details") or {}
            token = details.get("confirmation_token")
            if token:
                payload["confirmation_token"] = token
                status, j = await self._request(
                    "POST", f"/v1/aliases/{aid}/messages/{message_id}/reply", payload
                )
        if status >= 400:
            raise AgentMailError(f"reply failed: HTTP {status} ({j})")
        return j if isinstance(j, dict) else {}

    async def async_forward_message(
        self, message_id: str, to: list[str], include_attachments: bool = False
    ) -> dict[str, Any]:
        aid = await self._require_alias()
        payload = {"to": [{"email": e} for e in to], "include_attachments": include_attachments}
        status, j = await self._request("POST", f"/v1/aliases/{aid}/messages/{message_id}/forward", payload)
        err = j.get("error") if isinstance(j, dict) else None
        if err and err.get("code") == "CONFIRMATION_REQUIRED":
            details = err.get("details") or {}
            token = details.get("confirmation_token")
            if token:
                payload["confirmation_token"] = token
                status, j = await self._request(
                    "POST", f"/v1/aliases/{aid}/messages/{message_id}/forward", payload
                )
        if status >= 400:
            raise AgentMailError(f"forward failed: HTTP {status} ({j})")
        return j if isinstance(j, dict) else {}

    async def async_trash_message(self, message_id: str) -> None:
        aid = await self._require_alias()
        status, j = await self._request("DELETE", f"/v1/aliases/{aid}/messages/{message_id}")
        if status >= 400:
            raise AgentMailError(f"trash failed: HTTP {status} ({j})")

    async def async_delete_message(self, message_id: str) -> None:
        aid = await self._require_alias()
        status, j = await self._request("DELETE", f"/v1/aliases/{aid}/messages/{message_id}/permanent")
        if status >= 400:
            raise AgentMailError(f"permanent delete failed: HTTP {status} ({j})")

    async def async_download_attachment(self, message_id: str, attachment_id: str) -> bytes:
        aid = await self._require_alias()
        url = f"{API_BASE}/v1/aliases/{aid}/messages/{message_id}/attachments/{attachment_id}"
        headers = {"Authorization": f"Bearer {self._access_token}", "User-Agent": UA}
        async with self._session.get(url, headers=headers) as resp:
            if resp.status == 401 and self._refresh_token:
                await self._refresh()
                headers = {"Authorization": f"Bearer {self._access_token}", "User-Agent": UA}
                async with self._session.get(url, headers=headers) as resp2:
                    if resp2.status >= 400:
                        raise AgentMailError(f"attachment download failed: HTTP {resp2.status}")
                    return await resp2.read()
            if resp.status >= 400:
                raise AgentMailError(f"attachment download failed: HTTP {resp.status}")
            return await resp.read()

    async def async_watch_events(self, timeout: int = 25) -> dict[str, Any]:
        """Long-poll /events/wait — new-mail push. Raises on timeout."""
        aid = await self._require_alias()
        status, j = await self._request("GET", f"/v1/aliases/{aid}/events/wait?timeout={timeout}")
        if status >= 400:
            raise AgentMailError(f"events/wait failed: HTTP {status} ({j})")
        return j if isinstance(j, dict) else {}

    # ── ProviderRuntime surface ───────────────────────────────────────
    def status(self) -> str:
        return self._status

    def known_targets(self) -> list[dict[str, str]]:
        return self._known_contacts

    def selected_target(self) -> str:
        return self._selected_target

    async def select_target(self, target: str) -> None:
        self._selected_target = target

    def _record_contact(self, contact: Any) -> None:
        """Learn a sender/recipient as a usable send target."""
        if not isinstance(contact, dict):
            return
        email = str(contact.get("email", "")).strip()
        if not email:
            return
        name = str(contact.get("name") or email).strip()
        # select.py 期望 known_targets 项含 "target" 字段（对齐 QQ/飞书等 provider）
        entry = {"target": email, "name": name}
        self._known_contacts = [c for c in self._known_contacts if c["target"] != email]
        self._known_contacts.insert(0, entry)
        self._known_contacts = self._known_contacts[:50]
        if not self._selected_target:
            self._selected_target = email

    async def send_text(self, target: str, text: str, target_type: str = "") -> None:
        """Unified send entry: target = recipient email."""
        email = str(target or self._selected_target).strip()
        if not email:
            raise AgentMailError("send_text: target (email) is required")
        await self.async_send_message([email], subject=DEFAULT_SUBJECT, body=text)


# ── config flow support ────────────────────────────────────────────────
def _build_schema(current: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_AGENT_MAIL_ACCESS_TOKEN, default=current.get(CONF_AGENT_MAIL_ACCESS_TOKEN, "")
            ): str,
            vol.Optional(
                CONF_AGENT_MAIL_REFRESH_TOKEN, default=current.get(CONF_AGENT_MAIL_REFRESH_TOKEN, "")
            ): str,
            vol.Optional(
                CONF_AGENT_MAIL_ALIAS_ID, default=current.get(CONF_AGENT_MAIL_ALIAS_ID, "")
            ): str,
        }
    )


async def async_validate_config(hass: HomeAssistant, config: dict[str, Any]) -> None:
    token = str(config.get(CONF_AGENT_MAIL_ACCESS_TOKEN, "")).strip()
    if not token:
        raise ValueError("access_token is required")
    client = AgentMailClient(
        hass,
        token,
        str(config.get(CONF_AGENT_MAIL_REFRESH_TOKEN, "")).strip(),
        str(config.get(CONF_AGENT_MAIL_ALIAS_ID, "")).strip(),
    )
    try:
        await client.async_get_me()
    except AgentMailError as err:
        raise ValueError(f"token validation failed: {err}") from err


def _find_entry_for_subentry(hass: HomeAssistant, subentry_id: str) -> Any:
    """Locate the ConfigEntry owning this provider subentry (for token persistence)."""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if any(sub.subentry_id == subentry_id for sub in entry.subentries.values()):
            return entry
    return None


async def async_setup_provider(
    hass: HomeAssistant,
    config: dict[str, Any],
    *,
    agent_id: str,
    subentry_id: str,
) -> ProviderRuntime:
    async def _persist_tokens(access: str, refresh: str) -> None:
        """Write rotated tokens back to the subentry so they survive restarts."""
        entry = _find_entry_for_subentry(hass, subentry_id)
        if entry is None:
            return
        sub = next((s for s in entry.subentries.values() if s.subentry_id == subentry_id), None)
        if sub is None:
            return
        data = dict(sub.data)
        data[CONF_AGENT_MAIL_ACCESS_TOKEN] = access
        data[CONF_AGENT_MAIL_REFRESH_TOKEN] = refresh
        await hass.config_entries.async_update_subentry(entry, subentry_id, data=data)

    client = AgentMailClient(
        hass,
        str(config.get(CONF_AGENT_MAIL_ACCESS_TOKEN, "")).strip(),
        str(config.get(CONF_AGENT_MAIL_REFRESH_TOKEN, "")).strip(),
        str(config.get(CONF_AGENT_MAIL_ALIAS_ID, "")).strip(),
        subentry_id=subentry_id,
        on_tokens_refreshed=_persist_tokens,
    )
    await client.start()

    return ProviderRuntime(
        key=PROVIDER_AGENT_MAIL,
        title=PROVIDER_AGENT_MAIL,
        subentry_id=subentry_id,
        client=client,
        stop=client.stop,
        send_text=client.send_text,
        status=client.status,
        known_targets=client.known_targets,
        selected_target=client.selected_target,
        select_target=client.select_target,
    )


PROVIDER_SPEC = ProviderSpec(
    key=PROVIDER_AGENT_MAIL,
    schema_builder=_build_schema,
    validate_config=async_validate_config,
    setup_provider=async_setup_provider,
    flow_handler=AgentMailProviderSubentryFlow,
    allow_multiple=False,
)
