"""DingTalk QR device-registration setup flow.

Protocol from dsh-im device-auth.mjs (live-tested init):
  POST https://oapi.dingtalk.com/app/registration/init   {"source":"DING_DWS_CLAW"} -> nonce
  POST https://oapi.dingtalk.com/app/registration/begin  {"nonce"} -> device_code / verification_uri_complete
  POST https://oapi.dingtalk.com/app/registration/poll   {"device_code"} -> status WAITING|SUCCESS -> client_id/client_secret
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import (
    CONF_DINGTALK_CLIENT_ID,
    CONF_DINGTALK_CLIENT_SECRET,
    PROVIDER_DINGTALK,
)
from ...provider_flow import _existing_count, _load_channel_titles, _set_options

_LOGGER = logging.getLogger(__name__)

REG_BASE = "https://oapi.dingtalk.com/app/registration"
_REG_SOURCE = "DING_DWS_CLAW"
_POLL_INTERVAL_S = 5
_POLL_TIMEOUT_S = 7200

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
    import io

    import segno

    out = io.BytesIO()
    segno.make(text).save(out, kind="png", scale=6, border=2)
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")


class DingtalkScanSubentryFlow(ConfigSubentryFlow):
    """Scan-to-register flow for the DingTalk channel (device flow)."""

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
            _LOGGER.warning("DingTalk scan begin failed: %s", err)
            return self.async_abort(reason="cannot_connect")
        return await self.async_step_auth_wait(None)

    async def _async_post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        async with session.post(
            f"{REG_BASE}{path}", json=payload, headers={"accept": "application/json"}
        ) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"dingtalk {path} failed: HTTP {resp.status}")
            j = await resp.json()
        if j.get("errcode") not in (0, None):
            raise RuntimeError(f"dingtalk {path} error: {j.get('errmsg') or j}")
        return j

    async def async_step_set_options(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await _set_options(self, getattr(self, "_provider_spec", None), user_input)

    async def _async_begin(self) -> None:
        init = await self._async_post("/init", {"source": _REG_SOURCE})
        nonce = str(init.get("nonce") or "")
        if not nonce:
            raise RuntimeError("dingtalk init returned no nonce")
        begun = await self._async_post("/begin", {"nonce": nonce})
        device_code = str(begun.get("device_code") or "")
        auth_url = str(begun.get("verification_uri_complete") or "")
        if not device_code or not auth_url:
            raise RuntimeError(f"dingtalk begin returned invalid data: {begun}")
        self._current["device_code"] = device_code
        self._current["auth_url"] = auth_url
        self._current["user_code"] = str(begun.get("user_code", ""))
        self._current["expires_in"] = int(begun.get("expires_in", 7200))
        self._current["interval"] = int(begun.get("interval", 5))
        self._current["qr_data"] = await self.hass.async_add_executor_job(
            _make_qr_data_uri, auth_url
        )

    async def async_step_auth_wait(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        qr = str(self._current.get("qr_data", ""))
        placeholders = {
            "qr_markdown": f"![DingTalk QR]({qr})" if qr else "",
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
            _LOGGER.warning("DingTalk scan poll failed: %s", err)
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                errors={"base": "auth_not_confirmed"},
                description_placeholders=placeholders,
            )
        data = {
            CONF_DINGTALK_CLIENT_ID: creds["client_id"],
            CONF_DINGTALK_CLIENT_SECRET: creds["client_secret"],
        }
        title = str(user_input.get("title") or self._default_title()).strip() or self._default_title()
        return self.async_create_entry(
            title=title,
            data=data,
        )

    def _default_title(self) -> str:
        titles = _load_channel_titles(self.hass.config.language)
        base = titles.get(PROVIDER_DINGTALK, PROVIDER_DINGTALK)
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

            self.hass.async_create_task(_reload(), "cn_im_hub_dingtalk_reload")
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
        device_code = str(self._current["device_code"])
        deadline = time.monotonic() + int(self._current.get("expires_in", 7200))
        while time.monotonic() < deadline:
            j = await self._async_post("/poll", {"device_code": device_code})
            status = str(j.get("status", "")).upper()
            if status == "SUCCESS":
                client_id = str(j.get("client_id") or "")
                client_secret = str(j.get("client_secret") or "")
                if not client_id or not client_secret:
                    raise RuntimeError("dingtalk SUCCESS but credentials missing")
                return {"client_id": client_id, "client_secret": client_secret}
            if status in ("FAIL", "EXPIRED"):
                raise RuntimeError(f"dingtalk registration {status.lower()}")
            await asyncio.sleep(int(self._current.get("interval", 5)))
        raise RuntimeError("dingtalk authorization timed out")
