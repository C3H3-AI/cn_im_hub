"""Agent Mail device-flow (WeChat scan) setup flow.

Mirrors the Weixin QR flow skeleton (providers/wechat/flow.py) but for the
Tencent Agent Mail OAuth device flow: initiate -> show auth link + code ->
user scans with WeChat on agent.qq.com -> poll func=2 until authorized ->
create subentry with tokens.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import (
    CONF_AGENT_MAIL_ACCESS_TOKEN,
    CONF_AGENT_MAIL_ALIAS_ID,
    CONF_AGENT_MAIL_REFRESH_TOKEN,
    PROVIDER_AGENT_MAIL,
)
from ...provider_flow import _existing_count, _load_channel_titles

_LOGGER = logging.getLogger(__name__)

AUTH_BASE = "https://auth.agent.qq.com"
CLIENT_ID = "cli_002e8cd1f5e97858"  # public, version-bound constant
CLIENT_VERSION = "1.0.15"
# 服务器按 User-Agent 校验客户端身份（自定义 agent 标识会被拒为 "unsupported client"），
# 必须使用腾讯认可的 CLI 标识（实测 v1.0.15 组合）。
UA = "agently-cli/1.0.15 (windows/amd64; agent/workbuddy)"
POLL_INTERVAL_S = 5
POLL_TIMEOUT_S = 300


def _make_qr_data_uri(text: str) -> str:
    """本地生成二维码 PNG data URI（segno，已在 manifest requirements）。"""
    import base64
    import io

    import segno

    out = io.BytesIO()
    segno.make(text).save(out, kind="png", scale=6, border=2)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


class AgentMailProviderSubentryFlow(ConfigSubentryFlow):
    """Two-step WeChat-scan device-flow setup for the Tencent Agent Mail.

    agent.qq.com has no single-scan endpoint (scan_url is always empty).
    Real flow (verified against the live page): the authorization page
    embeds the official WeChat login QR (open.weixin.qq.com/connect/qrconnect).
    So the user first opens the browser_url (via our QR or the link), then
    scans the page's WeChat QR to sign in and confirm authorization.
    We keep both: our QR for the page + the link, then poll poll_url.
    """

    _provider_spec: Any
    _current: dict[str, Any]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._current = {}
        spec = getattr(self, "_provider_spec", None)
        if spec is not None and not spec.allow_multiple and _existing_count(self, spec) > 0:
            return self.async_abort(reason="already_configured")
        try:
            await self._async_prepare_device()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Agent Mail device flow init failed: %s", err)
            return self.async_abort(reason="cannot_connect")
        return await self.async_step_auth_wait(None)

    async def _async_prepare_device(self) -> None:
        """POST /oauth/device?func=1 -> poll_url / browser_url / input_code."""
        payload = {
            "app_id": CLIENT_ID,
            "cli_agentname": "WorkBuddy",
            "cli_agentua": "workbuddy",
            "cli_hostname": "homeassistant",
            "cli_ua": UA,
            "cli_version": CLIENT_VERSION,
        }
        session = async_get_clientsession(self.hass)
        async with session.post(
            f"{AUTH_BASE}/oauth/device?func=1", json=payload, headers={"User-Agent": UA}
        ) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"device flow init failed: HTTP {resp.status}")
            j = await resp.json()
        if not isinstance(j, dict) or not j.get("poll_url"):
            raise RuntimeError(f"device flow returned no poll_url: {j}")
        self._current["poll_url"] = j["poll_url"]
        self._current["browser_url"] = str(j.get("browser_url", ""))
        self._current["input_code"] = str(j.get("input_code", ""))
        # 授权页二维码：扫码打开 agent.qq.com 授权页（页内嵌微信登录二维码）
        self._current["qr_data"] = await self.hass.async_add_executor_job(
            _make_qr_data_uri, self._current["browser_url"]
        )

    async def async_step_auth_wait(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        qr = str(self._current.get("qr_data", ""))
        placeholders = {
            "qr_markdown": f"![Agent Mail QR]({qr})" if qr else "",
            "auth_url": str(self._current.get("browser_url", "")),
            "input_code": str(self._current.get("input_code", "")),
        }
        if user_input is None:
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema(
                    {vol.Optional("title", default=self._default_title()): str}
                ),
                description_placeholders=placeholders,
            )
        try:
            tokens = await self._async_poll_token(str(self._current.get("poll_url", "")))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Agent Mail device flow wait failed: %s", err)
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                errors={"base": "auth_not_confirmed"},
                description_placeholders=placeholders,
            )
        data = {
            CONF_AGENT_MAIL_ACCESS_TOKEN: str(tokens.get("access_token", "")),
            CONF_AGENT_MAIL_REFRESH_TOKEN: str(tokens.get("refresh_token", "")),
            CONF_AGENT_MAIL_ALIAS_ID: "",
        }
        title = str(user_input.get("title") or self._default_title()).strip() or self._default_title()
        return self.async_create_entry(
            title=title,
            data=data,
        )

    def _default_title(self) -> str:
        titles = _load_channel_titles(self.hass.config.language)
        base = titles.get(PROVIDER_AGENT_MAIL, PROVIDER_AGENT_MAIL)
        n = _existing_count(self, getattr(self, "_provider_spec", None))
        return base if n == 0 else f"{base} #{n + 1}"
    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Keep existing credentials; allow editing channel_agent_id, then reload."""
        from homeassistant.helpers import selector

        subentry = self._get_reconfigure_subentry()
        entry = self._get_entry()
        current_data = dict(subentry.data)
        if user_input is not None:
            new_data = {
                **current_data,
                "channel_agent_id": str(user_input.get("channel_agent_id", "") or ""),
            }
            result = self.async_update_and_abort(entry, subentry, data=new_data)

            async def _reload() -> None:
                await self.hass.config_entries.async_reload(entry.entry_id)

            self.hass.async_create_task(_reload(), "cn_im_hub_agent_mail_reload")
            return result
        agent_selector = selector.ConversationAgentSelector({"language": self.hass.config.language})
        schema = vol.Schema(
            {
                vol.Optional(
                    "channel_agent_id", default=current_data.get("channel_agent_id", "")
                ): agent_selector,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            description_placeholders={"hint": ""},
        )

    async def _async_poll_token(self, poll_url: str) -> dict[str, Any]:
        """GET poll_url (func=2&device_code=...) until status == authorized."""
        session = async_get_clientsession(self.hass)
        deadline = time.monotonic() + POLL_TIMEOUT_S
        last_status = "pending"
        while time.monotonic() < deadline:
            async with session.get(poll_url, headers={"User-Agent": UA}) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"device poll failed: HTTP {resp.status}")
                j = await resp.json()
            if not isinstance(j, dict):
                raise RuntimeError("device poll returned non-JSON response")
            last_status = str(j.get("status", "pending"))
            if last_status == "authorized":
                if not j.get("access_token"):
                    raise RuntimeError("authorized but response missing access_token")
                return j
            await asyncio.sleep(POLL_INTERVAL_S)
        raise RuntimeError(f"authorization timed out (last status: {last_status})")
