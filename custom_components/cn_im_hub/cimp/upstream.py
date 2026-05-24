"""CIMP upstream prompt builder."""
from __future__ import annotations

import json


def build_cimp_prompt(channel: str, supports: list[str]) -> str:
    """Build extra system prompt with CIMP capability declaration."""
    caps = ", ".join(supports)

    card_example = json.dumps({
        "type": "card",
        "scene": "confirm",
        "title": "\u64cd\u4f5c\u786e\u8ba4",
        "body": "\u786e\u5b9a\u8981\u6267\u884c\u5417\uff1f",
        "buttons": [{"id": "yes", "label": "\u786e\u8ba4", "style": "primary"}],
    })
    media_example = json.dumps({"type": "media", "kind": "image", "source": "camera.xxx"})
    update_example = json.dumps({
        "type": "state_update",
        "card_id": "...",
        "fields": {"status": "done"},
    })
    redirect_example = json.dumps({
        "type": "redirect",
        "target": "feishu:oc_xxx",
        "content": "\u6d88\u606f\u5185\u5bb9",
    })
    button_click_example = json.dumps({
        "type": "button_click",
        "button_id": "yes",
        "button_label": "\u786e\u8ba4",
        "conversation_id": "feishu:oc_xxx",
    })

    return (
        f"## CIMP \u534f\u8bae - \u7ed3\u6784\u5316\u901a\u4fe1\n"
        f"\u5f53\u524d\u6e20\u9053: {channel}\n"
        f"\u652f\u6301\u80fd\u529b: {caps}\n\n"
        f"\u4f60\u5fc5\u987b\u6309\u7167 CIMP JSON Lines \u683c\u5f0f\u8f93\u51fa\u56de\u590d\uff0c"
        f"\u6bcf\u884c\u4e00\u4e2a JSON \u5bf9\u8c61\u3002\u53ef\u4ee5\u8f93\u51fa\u591a\u884c\u3002\n\n"
        f"\u53ef\u7528\u5e27\u7c7b\u578b:\n"
        f"- {json.dumps({'type': 'text', 'content': '...'})} -- \u7eaf\u6587\u672c\u56de\u590d\n"
        f"- {card_example} -- \u4ea4\u4e92\u5361\u7247(\u5e26\u6309\u94ae)\n"
        f"- {media_example} -- \u5a92\u4f53\u6d88\u606f(\u56fe\u7247/\u89c6\u9891/GIF)\n"
        f"- {json.dumps({'type': 'voice', 'text': '...'})} -- \u8bed\u97f3\u6d88\u606f\n"
        f"- {update_example} -- \u5361\u7247\u72b6\u6001\u66f4\u65b0\n"
        f"- {redirect_example} -- \u8de8\u5bf9\u8bdd\u8f6c\u53d1\n\n"
        f"\u89c4\u5219:\n"
        f"1. \u81f3\u5c11\u8f93\u51fa\u4e00\u5e27\u3002\u4e0d\u786e\u5b9a\u65f6\u8f93\u51fa text \u5e27\n"
        f"2. \u7528\u6237\u70b9\u51fb\u5361\u7247\u6309\u94ae\u540e\uff0c\u4f60\u4f1a\u6536\u5230{button_click_example}\n"
        f"3. \u786e\u8ba4\u7c7b\u64cd\u4f5c\u4f18\u5148\u7528 card(scene=confirm) + yes/no \u6309\u94ae\n"
        f"4. \u5982\u6e20\u9053\u4e0d\u652f\u6301\u5361\u7247\uff0c\u7cfb\u7edf\u4f1a\u81ea\u52a8\u964d\u7ea7\u4e3a\u6587\u672c\n"
        f"5. button_click \u5e27\u5e26\u6709\u539f\u59cb conversation_id\uff0c"
        f"\u7528\u5b83\u6062\u590d\u5bf9\u8bdd\u4e0a\u4e0b\u6587\n"
    )
