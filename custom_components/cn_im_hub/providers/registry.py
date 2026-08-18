"""Provider registry with explicit imports."""

from __future__ import annotations

from functools import lru_cache

from ..provider_flow import build_simple_provider_flow
from .agent_mail import PROVIDER_SPEC as AGENT_MAIL_PROVIDER_SPEC
from .base import ProviderSpec
from .custom import PROVIDER_SPEC as CUSTOM_PROVIDER_SPEC
from .dingtalk import PROVIDER_SPEC as DINGTALK_PROVIDER_SPEC
from .feishu import PROVIDER_SPEC as FEISHU_PROVIDER_SPEC
from .qq import PROVIDER_SPEC as QQ_PROVIDER_SPEC
from .wechat import PROVIDER_SPEC as WECHAT_PROVIDER_SPEC
from .wecom import PROVIDER_SPEC as WECOM_PROVIDER_SPEC
from .xiaoyi import PROVIDER_SPEC as XIAOYI_PROVIDER_SPEC


_PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    WECOM_PROVIDER_SPEC,
    WECHAT_PROVIDER_SPEC,
    QQ_PROVIDER_SPEC,
    FEISHU_PROVIDER_SPEC,
    DINGTALK_PROVIDER_SPEC,
    XIAOYI_PROVIDER_SPEC,
    CUSTOM_PROVIDER_SPEC,
    AGENT_MAIL_PROVIDER_SPEC,
)


@lru_cache(maxsize=1)
def get_provider_specs() -> dict[str, ProviderSpec]:
    """Return all registered provider specs."""

    return {spec.key: spec for spec in _PROVIDER_SPECS}


def get_provider_spec(key: str) -> ProviderSpec:
    """Return a single provider spec."""

    return get_provider_specs()[key]


@lru_cache(maxsize=1)
def get_provider_flow_handlers() -> dict[str, type]:
    """Return subentry flow handlers for all providers."""

    handlers: dict[str, type] = {}
    for key, spec in get_provider_specs().items():
        handler = spec.flow_handler or build_simple_provider_flow(spec)
        setattr(handler, "_provider_spec", spec)
        handlers[key] = handler
    return handlers