"""Natural language parser and executor for IM providers."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from .conversation import ask_home_assistant
from .models import Command


def parse_command(text: str) -> Command | None:
    """Parse inbound text; only natural language conversation is supported."""
    text = text.strip()
    if not text:
        return None
    return Command(kind="conversation", target=text, payload={})


_IM_PREFIXES = {
    "wechat:": "WeChat",
    "feishu:": "Feishu",
    "dingtalk:": "DingTalk",
    "qq:": "QQ",
    "wecom:": "WeCom",
    "xiaoyi:": "XiaoYi",
}


async def execute_command(
    hass: HomeAssistant,
    command: Command,
    *,
    conversation_id: str,
    agent_id: str | None,
    extra_system_prompt: str | None = None,
) -> str:
    """Execute parsed command against Home Assistant."""
    if command.kind == "conversation":
        text = command.target
        for prefix, name in _IM_PREFIXES.items():
            if conversation_id.startswith(prefix):
                text = f"[IM:{name}|{conversation_id}] {text}"
                break
        return await ask_home_assistant(
            hass,
            text,
            conversation_id=conversation_id,
            agent_id=agent_id,
            extra_system_prompt=extra_system_prompt,
        )

    return "当前仅支持自然语言对话。"
