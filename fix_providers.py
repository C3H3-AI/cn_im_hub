#!/usr/bin/env python3
"""Fix cn_im_hub providers to support channel_agent_id"""
import os
import re

PROVIDERS_DIR = r"D:\ai-hub\integrations\cn_im_hub\custom_components\cn_im_hub\providers"

def fix_provider(provider_name):
    client_file = os.path.join(PROVIDERS_DIR, provider_name, "client.py")
    if not os.path.exists(client_file):
        print(f"File not found: {client_file}")
        return

    with open(client_file, 'r', encoding='utf-8') as f:
        content = f.read()

    # Add channel_agent_id to schema
    if 'channel_agent_id' not in content:
        # Find _build_schema function and add channel_agent_id
        pattern = r'(vol\.Optional\(_CONF_[A-Z_]+_SHOW_LIVE_PROGRESS[^)]+\): bool,)'
        replacement = r'\1\n            vol.Optional("channel_agent_id", default=current.get("channel_agent_id", "")): str,'
        content = re.sub(pattern, replacement, content)

    # Add effective_agent_id to setup_provider
    if 'effective_agent_id' not in content:
        # Find setup_provider function and add effective_agent_id
        pattern = r'(async def async_setup_provider\([^)]+\)[^:]*:)'
        replacement = r'\1\n    # Use channel-specific agent_id if configured\n    channel_agent_id = str(config.get("channel_agent_id", "")).strip()\n    effective_agent_id = channel_agent_id if channel_agent_id else agent_id'
        content = re.sub(pattern, replacement, content)

        # Replace agent_id with effective_agent_id in the function
        content = content.replace('conversation_agent_id=agent_id,', 'conversation_agent_id=effective_agent_id,')

    with open(client_file, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"Fixed {provider_name}")

# Fix all providers
providers = ['feishu', 'wechat', 'wecom', 'qq', 'dingtalk', 'xiaoyi']
for provider in providers:
    fix_provider(provider)

print("All providers fixed!")
