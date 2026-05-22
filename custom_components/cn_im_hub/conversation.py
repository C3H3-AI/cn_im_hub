"""Conversation helpers shared across providers."""

from __future__ import annotations

import inspect
import logging
from typing import Any

from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)


def extract_card(response: dict[str, Any] | None) -> dict[str, Any] | None:
    """Extract card dict from conversation response payload."""
    if not response:
        return None
    card = response.get("response", {}).get("card", None)
    if isinstance(card, dict):
        return card
    return None


def extract_card_from_obj(response: Any) -> dict[str, Any] | None:
    """Extract card from either dict or HA conversation response object."""
    if isinstance(response, dict):
        return extract_card(response)
    try:
        card = getattr(response.response, "card", None)
        if isinstance(card, dict):
            return card
    except Exception:
        pass
    if hasattr(response, "as_dict"):
        try:
            data = response.as_dict()
            if isinstance(data, dict):
                return extract_card(data)
        except Exception:
            pass
    return None


def extract_speech(response: dict[str, Any] | None) -> str:
    """Extract plain speech text from conversation response payload."""
    if not response:
        return ""
    speech = response.get("response", {}).get("speech", {}).get("plain", {})
    if isinstance(speech, dict):
        value = speech.get("speech")
        return value.strip() if isinstance(value, str) else ""
    if isinstance(speech, str):
        return speech.strip()
    return ""


def extract_speech_any(response: Any) -> str:
    """Extract speech from either dict or HA conversation response object."""
    if isinstance(response, dict):
        return extract_speech(response)

    text = ""
    try:
        plain = response.response.speech.get("plain", {})
        if isinstance(plain, dict):
            text = plain.get("speech", "")
        elif isinstance(plain, str):
            text = plain
    except Exception:
        pass

    if isinstance(text, str) and text.strip():
        return text.strip()

    if hasattr(response, "as_dict"):
        try:
            data = response.as_dict()
            if isinstance(data, dict):
                return extract_speech(data)
        except Exception:
            pass

    return ""


async def ask_home_assistant(
    hass: HomeAssistant,
    text: str,
    *,
    conversation_id: str,
    agent_id: str | None,
    extra_system_prompt: str | None = None,
) -> str:
    """Route text through Home Assistant conversation APIs."""
    resolved_agent_id = _normalize_agent_id_for_runtime(hass, str(agent_id or "").strip())
    try:
        from homeassistant.components import conversation as conversation_component

        if hasattr(conversation_component, "async_converse"):
            signature = inspect.signature(conversation_component.async_converse)
            kwargs: dict[str, Any] = {}
            if "hass" in signature.parameters:
                kwargs["hass"] = hass
            if "text" in signature.parameters:
                kwargs["text"] = text
            if "conversation_id" in signature.parameters and conversation_id:
                kwargs["conversation_id"] = conversation_id
            if "context" in signature.parameters:
                kwargs["context"] = Context()
            if "language" in signature.parameters:
                kwargs["language"] = hass.config.language
            if "agent_id" in signature.parameters and resolved_agent_id:
                kwargs["agent_id"] = resolved_agent_id
            if "extra_system_prompt" in signature.parameters and extra_system_prompt:
                kwargs["extra_system_prompt"] = extra_system_prompt

            result = await conversation_component.async_converse(**kwargs)
            reply = extract_speech_any(result)
            if reply:
                return reply
    except Exception as err:
        _LOGGER.debug("cn_im_hub async_converse failed with agent_id=%s: %s", resolved_agent_id, err)

    if hass.services.has_service("conversation", "process"):
        data: dict[str, Any] = {
            "text": text,
            "conversation_id": conversation_id,
            "language": hass.config.language,
        }
        if resolved_agent_id:
            data["agent_id"] = resolved_agent_id
        result = await hass.services.async_call(
            "conversation",
            "process",
            data,
            blocking=True,
            return_response=True,
        )
        reply = extract_speech(result)
        if reply:
            return reply

    return "暂时无法生成回复，请检查当前 conversation agent 配置。"

async def ask_home_assistant_with_card(
    hass: HomeAssistant,
    text: str,
    *,
    conversation_id: str,
    agent_id: str | None,
    extra_system_prompt: str | None = None,
) -> tuple[str, dict[str, Any] | None]:
    """Route text through HA conversation and return (text, card_or_None)."""
    resolved_agent_id = _normalize_agent_id_for_runtime(hass, str(agent_id or "").strip())
    try:
        from homeassistant.components import conversation as conversation_component

        if hasattr(conversation_component, "async_converse"):
            signature = inspect.signature(conversation_component.async_converse)
            kwargs: dict[str, Any] = {}
            if "hass" in signature.parameters:
                kwargs["hass"] = hass
            if "text" in signature.parameters:
                kwargs["text"] = text
            if "conversation_id" in signature.parameters and conversation_id:
                kwargs["conversation_id"] = conversation_id
            if "context" in signature.parameters:
                kwargs["context"] = Context()
            if "language" in signature.parameters:
                kwargs["language"] = hass.config.language
            if "agent_id" in signature.parameters and resolved_agent_id:
                kwargs["agent_id"] = resolved_agent_id
            if "extra_system_prompt" in signature.parameters and extra_system_prompt:
                kwargs["extra_system_prompt"] = extra_system_prompt

            result = await conversation_component.async_converse(**kwargs)
            reply = extract_speech_any(result)
            card = extract_card_from_obj(result)
            if reply:
                return reply, card
    except Exception as err:
        _LOGGER.debug("cn_im_hub async_converse failed with agent_id=%s: %s", resolved_agent_id, err)

    if hass.services.has_service("conversation", "process"):
        data: dict[str, Any] = {
            "text": text,
            "conversation_id": conversation_id,
            "language": hass.config.language,
        }
        if resolved_agent_id:
            data["agent_id"] = resolved_agent_id
        result = await hass.services.async_call(
            "conversation",
            "process",
            data,
            blocking=True,
            return_response=True,
        )
        reply = extract_speech(result)
        card = extract_card(result)
        if reply:
            return reply, card

    return "暂时无法生成回复，请检查当前 conversation agent 配置。", None


def _normalize_agent_id_for_runtime(hass: HomeAssistant, agent_id: str) -> str:
    candidate = agent_id.strip()
    if not candidate or candidate == "conversation.home_assistant":
        return candidate

    if candidate.startswith("conversation."):
        return candidate

    entity_registry = er.async_get(hass)
    for entry in entity_registry.entities.values():
        if entry.domain != "conversation":
            continue
        if entry.config_entry_id == candidate:
            return entry.entity_id

    return candidate