"""Config flow for CN IM Hub."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow as HAConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import CONF_AGENT_ID, DOMAIN
from .providers.registry import get_provider_flow_handlers

_LOGGER = logging.getLogger(__name__)


async def _get_preferred_agent_id(hass) -> str:
    try:
        from homeassistant.components.assist_pipeline.pipeline import async_get_pipeline

        pipeline = async_get_pipeline(hass)
        if isinstance(pipeline.conversation_engine, str) and pipeline.conversation_engine:
            return pipeline.conversation_engine
    except Exception as err:
        _LOGGER.debug("Unable to resolve preferred assist pipeline: %r", err)
    return ""


def _normalize_agent_id_for_storage(hass, agent_id: str) -> str:
    candidate = agent_id.strip()
    if not candidate or candidate == "conversation.home_assistant":
        return candidate

    if candidate.startswith("conversation."):
        entity = er.async_get(hass).async_get(candidate)
        if entity and entity.config_entry_id:
            return entity.config_entry_id

    return candidate


def _agent_selector(hass) -> selector.ConversationAgentSelector:
    return selector.ConversationAgentSelector({"language": hass.config.language})


class ConfigFlow(HAConfigFlow, domain=DOMAIN):
    """Hub-level config flow; providers are loaded dynamically from registry."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        preferred_agent = await _get_preferred_agent_id(self.hass)
        if user_input is not None:
            agent_id = str(user_input.get(CONF_AGENT_ID, "")).strip()
            if not agent_id:
                errors["base"] = "agent_id_required"
            else:
                return self.async_create_entry(
                    title="",
                    data={},
                    options={CONF_AGENT_ID: _normalize_agent_id_for_storage(self.hass, agent_id)},
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_AGENT_ID, default=preferred_agent): _agent_selector(self.hass)}
            ),
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> "OptionsFlowHandler":
        return OptionsFlowHandler(config_entry)

    @classmethod
    @callback
    def async_get_supported_subentry_types(cls, config_entry: ConfigEntry) -> dict[str, type]:
        return get_provider_flow_handlers()


class OptionsFlowHandler(OptionsFlow):
    """Manage global conversation agent only."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        preferred_agent = await _get_preferred_agent_id(self.hass)
        current = str(
            self._config_entry.options.get(
                CONF_AGENT_ID,
                self._config_entry.data.get(CONF_AGENT_ID, preferred_agent),
            )
        ).strip()

        if user_input is not None:
            agent_id = str(user_input.get(CONF_AGENT_ID, "")).strip()
            if not agent_id:
                errors["base"] = "agent_id_required"
            else:
                return self.async_create_entry(
                    title="",
                    data={CONF_AGENT_ID: _normalize_agent_id_for_storage(self.hass, agent_id)},
                )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {vol.Required(CONF_AGENT_ID, default=current): _agent_selector(self.hass)}
            ),
            errors=errors,
        )
