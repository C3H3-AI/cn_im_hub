"""Feishu QR device-registration setup flow.

Protocol: official @larksuiteoapi/node-sdk registerApp (device flow), live-tested:
  POST https://accounts.feishu.cn/oauth/v1/app/registration
    begin -> {device_code, user_code, verification_uri_complete, expires_in, interval}
    poll  -> HTTP 400 authorization_pending | {client_id, client_secret, user_info}
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import CONF_FEISHU_APP_ID, CONF_FEISHU_APP_SECRET, PROVIDER_FEISHU
from ...provider_flow import _existing_count, _load_channel_titles, _set_options

_LOGGER = logging.getLogger(__name__)

REG_URL = "https://accounts.feishu.cn/oauth/v1/app/registration"
_BEGIN_PARAMS = {
    "action": "begin",
    "archetype": "PersonalAgent",
    "auth_method": "client_secret",
    "request_user_info": "open_id",
}
_POLL_INTERVAL_S = 5
_POLL_TIMEOUT_S = 3600

_METHOD_SCHEMA = vol.Schema(
    {
        vol.Required("method", default="scan"): selector.SelectSelector(
            selector.SelectSelectorConfig(
                options=[
                    selector.SelectOptionDict(value="scan", label="扫码创建 / 绑定"),
                    selector.SelectOptionDict(value="manual", label="手动填写凭据"),
                ],
                mode=selector.SelectSelectorMode.LIST,
            )
        ),
    }
)


def _make_qr_data_uri(text: str) -> str:
    """Local QR PNG data URI (segno, already a manifest dependency)."""
    import base64
    import io

    import segno

    out = io.BytesIO()
    segno.make(text).save(out, kind="png", scale=6, border=2)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


class FeishuScanSubentryFlow(ConfigSubentryFlow):
    """Scan-to-register flow for the Feishu channel (device flow)."""

    _provider_spec: Any
    _current: dict[str, Any]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        self._current = {}
        spec = getattr(self, "_provider_spec", None)
        if spec is not None and not spec.allow_multiple and _existing_count(self, spec) > 0:
            return self.async_abort(reason="already_configured")
        if user_input is None:
            return self.async_show_form(
                step_id="user",
                data_schema=_METHOD_SCHEMA,
            )
        if str(user_input.get("method")) == "manual":
            return await _set_options(self, spec, None)
        try:
            await self._async_begin()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Feishu scan begin failed: %s", err)
            return self.async_abort(reason="cannot_connect")
        return await self.async_step_auth_wait(None)

    async def async_step_set_options(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await _set_options(self, getattr(self, "_provider_spec", None), user_input)

    async def _async_begin(self) -> None:
        session = async_get_clientsession(self.hass)
        async with session.post(REG_URL, data=_BEGIN_PARAMS) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"feishu begin failed: HTTP {resp.status}")
            j = await resp.json()
        if not isinstance(j, dict) or not j.get("device_code") or not j.get("verification_uri_complete"):
            raise RuntimeError(f"feishu begin returned invalid data: {j}")
        self._current["device_code"] = j["device_code"]
        self._current["auth_url"] = j["verification_uri_complete"]
        self._current["user_code"] = str(j.get("user_code", ""))
        self._current["expires_in"] = int(j.get("expires_in", 3600))
        self._current["interval"] = int(j.get("interval", 5))
        self._current["qr_data"] = await self.hass.async_add_executor_job(
            _make_qr_data_uri, self._current["auth_url"]
        )

    async def async_step_auth_wait(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        qr = str(self._current.get("qr_data", ""))
        placeholders = {
            "qr_markdown": f"![Feishu QR]({qr})" if qr else "",
            "auth_url": str(self._current.get("auth_url", "")),
            "user_code": str(self._current.get("user_code", "")),
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
            creds = await self._async_poll()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Feishu scan poll failed: %s", err)
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                errors={"base": "auth_not_confirmed"},
                description_placeholders=placeholders,
            )
        data = {
            CONF_FEISHU_APP_ID: creds["client_id"],
            CONF_FEISHU_APP_SECRET: creds["client_secret"],
        }
        title = str(user_input.get("title") or self._default_title()).strip() or self._default_title()
        return self.async_create_entry(
            title=title,
            data=data,
        )

    def _default_title(self) -> str:
        titles = _load_channel_titles(self.hass.config.language)
        base = titles.get(PROVIDER_FEISHU, PROVIDER_FEISHU)
        n = _existing_count(self, getattr(self, "_provider_spec", None))
        return base if n == 0 else f"{base} #{n + 1}"
    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        entry = self._get_entry()
        if user_input is not None:
            new_data = {
                **dict(subentry.data),
                "channel_agent_id": str(user_input.get("channel_agent_id", "") or ""),
            }
            result = self.async_update_and_abort(entry, subentry, data=new_data)

            async def _reload() -> None:
                await self.hass.config_entries.async_reload(entry.entry_id)

            self.hass.async_create_task(_reload(), "cn_im_hub_feishu_reload")
            return result
        from homeassistant.helpers import selector

        agent_selector = selector.ConversationAgentSelector({"language": self.hass.config.language})
        schema = vol.Schema(
            {
                vol.Optional(
                    "channel_agent_id", default=dict(subentry.data).get("channel_agent_id", "")
                ): agent_selector,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            description_placeholders={"hint": ""},
        )

    async def _async_poll(self) -> dict[str, str]:
        session = async_get_clientsession(self.hass)
        device_code = str(self._current["device_code"])
        deadline = time.monotonic() + int(self._current.get("expires_in", 3600))
        while time.monotonic() < deadline:
            async with session.post(
                REG_URL, data={"action": "poll", "device_code": device_code}
            ) as resp:
                if resp.status == 400:
                    # RFC 8628: authorization_pending / slow_down
                    await asyncio.sleep(int(self._current.get("interval", 5)))
                    continue
                if resp.status >= 400:
                    raise RuntimeError(f"feishu poll failed: HTTP {resp.status}")
                j = await resp.json()
            if not isinstance(j, dict):
                raise RuntimeError("feishu poll returned non-JSON")
            if j.get("client_id") and j.get("client_secret"):
                return {"client_id": j["client_id"], "client_secret": j["client_secret"]}
            await asyncio.sleep(int(self._current.get("interval", 5)))
        raise RuntimeError("feishu authorization timed out")
