"""Provider spec definitions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import voluptuous as vol

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from ..models import ProviderRuntime


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Self-contained provider definition."""

    key: str
    schema_builder: Callable[[dict[str, Any]], vol.Schema]
    validate_config: Callable[[HomeAssistant, dict[str, Any]], Awaitable[None]]
    setup_provider: Callable[..., Awaitable[ProviderRuntime]]
    flow_handler: type[Any] | None = None
    allow_multiple: bool = False