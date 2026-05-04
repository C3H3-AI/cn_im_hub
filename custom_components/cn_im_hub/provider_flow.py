"""Shared provider flow helpers."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigSubentryFlow, SubentryFlowResult

from .providers.base import ProviderSpec

_LOGGER = logging.getLogger(__name__)


class BaseProviderSubentryFlow(ConfigSubentryFlow):
    """Shared base for provider subentry flows."""

    _provider_spec: ProviderSpec
    _current: dict[str, Any]

    @property
    def _is_new(self) -> bool:
        return self.source == "user"

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        if not self._provider_spec.allow_multiple:
            existing = [
                subentry
                for subentry in self._get_entry().subentries.values()
                if subentry.subentry_type == self._provider_spec.key
            ]
            if existing:
                return self.async_abort(reason="already_configured")
        self._current = {}
        return await self.async_step_set_options(user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        self._current = dict(self._get_reconfigure_subentry().data)
        return await self.async_step_set_options(user_input)

    async def _async_complete(self, data: dict[str, str]) -> SubentryFlowResult:
        if self._is_new:
            return self.async_create_entry(title=self._provider_spec.title, data=data)
        return self.async_update_and_abort(
            self._get_entry(),
            self._get_reconfigure_subentry(),
            data=data,
        )


def _normalize_user_input(data: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key, value in data.items():
        if isinstance(value, str):
            normalized[key] = value.strip()
        else:
            normalized[key] = value
    return normalized


class SimpleProviderSubentryFlow(BaseProviderSubentryFlow):
    """Generic single-step provider flow."""

    async def async_step_set_options(
        self, user_input: dict[str, Any] | None
    ) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalize_user_input(user_input)
            try:
                await self._provider_spec.validate_config(self.hass, data)
            except Exception as err:
                _LOGGER.warning("Provider validation failed (%s): %s", self._provider_spec.key, err)
                errors["base"] = "cannot_connect"
            else:
                return await self._async_complete(data)
            self._current = data

        return self.async_show_form(
            step_id="set_options",
            data_schema=self._provider_spec.schema_builder(self._current),
            errors=errors,
        )


def build_simple_provider_flow(spec: ProviderSpec) -> type[ConfigSubentryFlow]:
    """Build a generic flow handler for a provider spec."""

    class _ProviderFlow(SimpleProviderSubentryFlow):
        _provider_spec = spec

    _ProviderFlow.__name__ = f"{spec.title.replace(' ', '')}ProviderSubentryFlow"
    return _ProviderFlow
