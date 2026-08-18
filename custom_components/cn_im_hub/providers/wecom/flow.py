"""WeCom (Enterprise WeChat) QR registration setup flow.

Protocol from dsh-im wecom/qr-auth.mjs (live-tested generate):
  GET https://work.weixin.qq.com/ai/qc/generate?source=deepseek-harness&plat=1
    -> {data: {scode, auth_url}}
  GET https://work.weixin.qq.com/ai/qc/query_result?scode=<scode>
    -> {data: {status: success|..., bot_info: {botid, secret}}}
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

from ...const import CONF_WECOM_BOT_ID, CONF_WECOM_SECRET, PROVIDER_WECOM
from ...provider_flow import _existing_count, _load_channel_titles, _set_options

_LOGGER = logging.getLogger(__name__)

GENERATE_URL = "https://work.weixin.qq.com/ai/qc/generate"
POLL_URL = "https://work.weixin.qq.com/ai/qc/query_result"
_REG_SOURCE = "deepseek-harness"
_PLATFORM = "1"
_POLL_INTERVAL_S = 3
_POLL_TIMEOUT_S = 600

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


class WecomScanSubentryFlow(ConfigSubentryFlow):
    """Scan-to-create flow for the WeCom channel."""

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
            _LOGGER.warning("WeCom scan begin failed: %s", err)
            return self.async_abort(reason="cannot_connect")
        return await self.async_step_auth_wait(None)

    async def async_step_set_options(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await _set_options(self, getattr(self, "_provider_spec", None), user_input)

    async def _async_begin(self) -> None:
        session = async_get_clientsession(self.hass)
        async with session.get(
            GENERATE_URL, params={"source": _REG_SOURCE, "plat": _PLATFORM}
        ) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"wecom generate failed: HTTP {resp.status}")
            j = await resp.json()
        data = j.get("data") or {}
        scode = str(data.get("scode") or "")
        auth_url = str(data.get("auth_url") or "")
        if not scode or not auth_url:
            raise RuntimeError(f"wecom generate returned invalid data: {j}")
        self._current["scode"] = scode
        self._current["auth_url"] = auth_url
        self._current["qr_data"] = await self.hass.async_add_executor_job(
            _make_qr_data_uri, auth_url
        )

    async def async_step_auth_wait(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        qr = str(self._current.get("qr_data", ""))
        placeholders = {
            "qr_markdown": f"![WeCom QR]({qr})" if qr else "",
            "auth_url": str(self._current.get("auth_url", "")),
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
            _LOGGER.warning("WeCom scan poll failed: %s", err)
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                errors={"base": "auth_not_confirmed"},
                description_placeholders=placeholders,
            )
        data = {
            CONF_WECOM_BOT_ID: creds["bot_id"],
            CONF_WECOM_SECRET: creds["secret"],
        }
        title = str(user_input.get("title") or self._default_title()).strip() or self._default_title()
        return self.async_create_entry(
            title=title,
            data=data,
        )

    def _default_title(self) -> str:
        titles = _load_channel_titles(self.hass.config.language)
        base = titles.get(PROVIDER_WECOM, PROVIDER_WECOM)
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

            self.hass.async_create_task(_reload(), "cn_im_hub_wecom_reload")
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
        scode = str(self._current["scode"])
        deadline = time.monotonic() + _POLL_TIMEOUT_S
        while time.monotonic() < deadline:
            async with session.get(POLL_URL, params={"scode": scode}) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"wecom query_result failed: HTTP {resp.status}")
                j = await resp.json()
            data = j.get("data") or {}
            state = str(data.get("status", "")).lower()
            if state == "success":
                bot_info = data.get("bot_info") or {}
                bot_id = str(bot_info.get("botid") or "")
                secret = str(bot_info.get("secret") or "")
                if not bot_id or not secret:
                    raise RuntimeError("wecom success but bot credentials missing")
                return {"bot_id": bot_id, "secret": secret}
            if state in ("expired", "timeout"):
                raise RuntimeError("wecom QR expired")
            if state in ("fail", "failed", "error"):
                raise RuntimeError("wecom QR failed")
            await asyncio.sleep(_POLL_INTERVAL_S)
        raise RuntimeError("wecom authorization timed out")
