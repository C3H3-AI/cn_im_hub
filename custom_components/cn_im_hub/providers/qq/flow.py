"""QQ QR bind setup flow.

Protocol from @tencent-connect/qqbot-connector (live-tested create_bind_task):
  POST https://q.qq.com/lite/create_bind_task {"key": b64(32B)} -> {retcode, data.task_id}
  QR content: https://q.qq.com/qqbot/openclaw/connect.html?task_id=<id>&_wv=2
  POST https://q.qq.com/lite/poll_bind_result {"task_id"} ->
    data.status 2=COMPLETED {bot_appid, bot_encrypt_secret} 3=EXPIRED
  Secret: AES-256-GCM (key=b64(32B); iv=first12B; tag=last16B) of bot_encrypt_secret
"""

from __future__ import annotations

import asyncio
import base64
import logging
import secrets
import time
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from ...const import CONF_QQ_APP_ID, CONF_QQ_CLIENT_SECRET, PROVIDER_QQ
from ...provider_flow import _existing_count, _load_channel_titles, _set_options

_LOGGER = logging.getLogger(__name__)

CREATE_URL = "https://q.qq.com/lite/create_bind_task"
POLL_URL = "https://q.qq.com/lite/poll_bind_result"
CONNECT_URL = "https://q.qq.com/qqbot/openclaw/connect.html"
_STATUS_COMPLETED = 2
_STATUS_EXPIRED = 3
_POLL_INTERVAL_S = 2
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


def _decrypt_secret(encrypted_b64: str, key_b64: str) -> str:
    """AES-256-GCM decrypt (mirrors @tencent-connect/qqbot-connector)."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    key = base64.b64decode(key_b64)
    data = base64.b64decode(encrypted_b64)
    iv, tag, ciphertext = data[:12], data[-16:], data[12:-16]
    decryptor = Cipher(algorithms.AES(key), modes.GCM(iv, tag)).decryptor()
    return decryptor.update(ciphertext) + decryptor.finalize()


class QQScanSubentryFlow(ConfigSubentryFlow):
    """Scan-to-bind flow for the QQ channel."""

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
            _LOGGER.warning("QQ scan begin failed: %s", err)
            return self.async_abort(reason="cannot_connect")
        return await self.async_step_auth_wait(None)

    async def async_step_set_options(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        return await _set_options(self, getattr(self, "_provider_spec", None), user_input)

    async def _async_begin(self) -> None:
        key_b64 = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        self._current["key_b64"] = key_b64
        session = async_get_clientsession(self.hass)
        async with session.post(CREATE_URL, json={"key": key_b64}) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"QQ create_bind_task failed: HTTP {resp.status}")
            j = await resp.json()
        task_id = (j.get("data") or {}).get("task_id")
        if j.get("retcode") != 0 or not task_id:
            raise RuntimeError(f"QQ create_bind_task error: {j}")
        self._current["task_id"] = task_id
        self._current["auth_url"] = f"{CONNECT_URL}?task_id={task_id}&_wv=2"
        self._current["qr_data"] = await self.hass.async_add_executor_job(
            _make_qr_data_uri, self._current["auth_url"]
        )

    async def async_step_auth_wait(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        qr = str(self._current.get("qr_data", ""))
        placeholders = {
            "qr_markdown": f"![QQ QR]({qr})" if qr else "",
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
            _LOGGER.warning("QQ scan poll failed: %s", err)
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                errors={"base": "auth_not_confirmed"},
                description_placeholders=placeholders,
            )
        data = {
            CONF_QQ_APP_ID: creds["app_id"],
            CONF_QQ_CLIENT_SECRET: creds["app_secret"],
        }
        title = str(user_input.get("title") or self._default_title()).strip() or self._default_title()
        return self.async_create_entry(
            title=title,
            data=data,
        )

    def _default_title(self) -> str:
        titles = _load_channel_titles(self.hass.config.language)
        base = titles.get(PROVIDER_QQ, PROVIDER_QQ)
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

            self.hass.async_create_task(_reload(), "cn_im_hub_qq_reload")
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
        task_id = str(self._current["task_id"])
        deadline = time.monotonic() + _POLL_TIMEOUT_S
        while time.monotonic() < deadline:
            async with session.post(POLL_URL, json={"task_id": task_id}) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"QQ poll_bind_result failed: HTTP {resp.status}")
                j = await resp.json()
            data = j.get("data") or {}
            status = data.get("status")
            if status == _STATUS_COMPLETED:
                encrypted = data.get("bot_encrypt_secret") or ""
                app_id = str(data.get("bot_appid") or "")
                if not encrypted or not app_id:
                    raise RuntimeError("QQ bind completed but credentials missing")
                app_secret = await self.hass.async_add_executor_job(
                    _decrypt_secret, encrypted, str(self._current["key_b64"])
                )
                return {"app_id": app_id, "app_secret": app_secret}
            if status == _STATUS_EXPIRED:
                raise RuntimeError("QQ QR code expired")
            await asyncio.sleep(_POLL_INTERVAL_S)
        raise RuntimeError("QQ authorization timed out")
