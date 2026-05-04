"""Weixin QR login flow."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult
from homeassistant.helpers.storage import Store

from ..const import CONF_WECHAT_ACCOUNT_ID, CONF_WECHAT_BASE_URL, CONF_WECHAT_TOKEN, CONF_WECHAT_USER_ID, WECHAT_DEFAULT_BASE_URL
from .wechat_auth import async_start_weixin_login, async_wait_weixin_login

_LOGGER = logging.getLogger(__name__)
_ACCOUNT_INDEX_STORE_VERSION = 1
_ACCOUNT_INDEX_STORE_KEY = "cn_im_hub_wechat_accounts"


class WeixinProviderSubentryFlow(ConfigSubentryFlow):
    """QR login based setup flow for Weixin channel."""

    _provider_spec: Any
    _current: dict[str, Any]

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._current = {CONF_WECHAT_BASE_URL: WECHAT_DEFAULT_BASE_URL}
        await self._async_prepare_qr()
        return await self.async_step_auth_wait(None)

    async def async_step_auth_wait(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        placeholders = {
            "qr_markdown": f"![Weixin QR]({self._current.get('wechat_qr_data_url', '')})"
            if self._current.get("wechat_qr_data_url")
            else "",
            "qr_url": str(self._current.get("wechat_qr_url", "")),
        }
        if user_input is None:
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                description_placeholders=placeholders,
            )
        try:
            result = await async_wait_weixin_login(
                self.hass,
                login=self._current["wechat_login_session"],
                base_url=str(self._current.get(CONF_WECHAT_BASE_URL, WECHAT_DEFAULT_BASE_URL)),
            )
        except Exception as err:
            _LOGGER.warning("Weixin QR login wait failed: %s", err)
            return self.async_show_form(
                step_id="auth_wait",
                data_schema=vol.Schema({}),
                errors={"base": "auth_not_confirmed"},
                description_placeholders=placeholders,
            )

        data = {
            CONF_WECHAT_TOKEN: result.token,
            CONF_WECHAT_ACCOUNT_ID: result.account_id,
            CONF_WECHAT_USER_ID: result.user_id,
            CONF_WECHAT_BASE_URL: result.base_url or str(self._current.get(CONF_WECHAT_BASE_URL, WECHAT_DEFAULT_BASE_URL)),
        }
        await self._async_update_account_index(data)
        return self.async_create_entry(
            title=self._build_entry_title(data),
            data=data,
        )

    @staticmethod
    def _build_entry_title(data: dict[str, str]) -> str:
        account_id = str(data.get(CONF_WECHAT_ACCOUNT_ID, "")).strip()
        user_id = str(data.get(CONF_WECHAT_USER_ID, "")).strip()
        suffix = account_id or user_id
        if suffix:
            return f"WeChat ({suffix})"
        return "WeChat"

    async def _async_prepare_qr(self) -> None:
        result = await async_start_weixin_login(
            self.hass,
            base_url=str(self._current.get(CONF_WECHAT_BASE_URL, WECHAT_DEFAULT_BASE_URL)),
        )
        self._current["wechat_login_session"] = result
        self._current["wechat_qr_url"] = result.qrcode_url
        self._current["wechat_qr_data_url"] = result.qrcode_data_url

    async def _async_update_account_index(self, data: dict[str, str]) -> None:
        store: Store[dict[str, dict[str, str]]] = Store(
            self.hass,
            _ACCOUNT_INDEX_STORE_VERSION,
            _ACCOUNT_INDEX_STORE_KEY,
        )
        current = await store.async_load() or {}
        user_id = str(data.get(CONF_WECHAT_USER_ID, "")).strip()
        account_id = str(data.get(CONF_WECHAT_ACCOUNT_ID, "")).strip()
        if user_id:
            stale_keys = [key for key, value in current.items() if key != account_id and value.get(CONF_WECHAT_USER_ID) == user_id]
            for key in stale_keys:
                current.pop(key, None)
        if account_id:
            current[account_id] = {
                CONF_WECHAT_USER_ID: user_id,
                CONF_WECHAT_BASE_URL: str(data.get(CONF_WECHAT_BASE_URL, WECHAT_DEFAULT_BASE_URL)),
            }
        await store.async_save(current)
