"""Real-time thinking status manager for streaming card updates.

This module manages the thinking process display in Feishu cards,
allowing Claw to report its thinking status in real-time.

Usage:
    1. Start thinking: manager = ThinkingManager.start(...)
    2. Update status: await manager.update_status("正在搜索...")
    3. Finish: await manager.finish(final_result)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

_LOGGER = logging.getLogger(__name__)


@dataclass
class ThinkingSession:
    """Represents an active thinking session."""
    message_id: str
    conversation_id: str
    chat_id: str
    receive_type: str
    api_client: Any
    current_status: str = "🤔 正在思考..."
    start_time: float = field(default_factory=lambda: asyncio.get_event_loop().time())
    _update_task: asyncio.Task | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def update(self, status: str, details: str = "") -> None:
        """Update the thinking status displayed in the card."""
        async with self._lock:
            self.current_status = status
            await self._render(status, details)

    async def _render(self, status: str, details: str = "") -> None:
        """Render current status to the streaming card."""
        # Build thinking card content
        elements = [
            {
                "tag": "markdown",
                "element_id": "thinking_status",
                "content": f"**{status}**",
            }
        ]
        if details:
            elements.append({
                "tag": "markdown",
                "element_id": "thinking_details",
                "content": details,
            })

        # Add elapsed time indicator
        elapsed = int(asyncio.get_event_loop().time() - self.start_time)
        elements.append({
            "tag": "note",
            "element_id": "thinking_time",
            "elements": [{"tag": "plain_text", "content": f"⏱️ {elapsed}秒"}],
        })

        card = {"body": {"elements": elements}}

        try:
            await self.api_client.async_patch_card(
                message_id=self.message_id,
                card=card,
            )
        except Exception as err:
            _LOGGER.warning("Failed to update thinking card: %s", err)

    async def finish(self, final_result: str | None = None) -> None:
        """Finish thinking and optionally show final result."""
        if self._update_task:
            self._update_task.cancel()
            try:
                await self._update_task
            except asyncio.CancelledError:
                pass

        # Final update shows completion
        if final_result:
            elements = [
                {
                    "tag": "markdown",
                    "element_id": "thinking_status",
                    "content": "✅ 思考完成",
                },
                {"tag": "hr"},
                {
                    "tag": "markdown",
                    "element_id": "final_result",
                    "content": final_result,
                },
            ]
        else:
            elements = [
                {
                    "tag": "markdown",
                    "element_id": "thinking_status",
                    "content": "✅ 思考完成",
                }
            ]

        card = {"body": {"elements": elements}}

        try:
            await self.api_client.async_patch_card(
                message_id=self.message_id,
                card=card,
            )
        except Exception as err:
            _LOGGER.warning("Failed to finalize thinking card: %s", err)


class ThinkingManager:
    """Manages thinking sessions for multiple conversations."""

    _sessions: dict[str, ThinkingSession] = {}

    @classmethod
    async def start(
        cls,
        *,
        api_client: Any,
        receive_id: str,
        receive_type: str,
        conversation_id: str,
        title: str = "Claw AI 助手",
        initial_status: str = "🤔 正在思考...",
    ) -> ThinkingSession:
        """Start a new thinking session with a streaming card.

        Args:
            api_client: FeishuApiClient instance.
            receive_id: Feishu chat_id or open_id.
            receive_type: "chat_id" or "open_id".
            conversation_id: Conversation ID for tracking.
            title: Card title.
            initial_status: Initial thinking status.

        Returns:
            ThinkingSession instance for updates.
        """
        # Create initial streaming card
        initial_card = {
            "schema": "2.0",
            "config": {
                "streaming_mode": True,
                "summary": {"content": "Claw 正在思考..."},
            },
            "header": {
                "title": {"content": title, "tag": "plain_text"},
                "template": "blue",
            },
            "body": {
                "elements": [
                    {
                        "tag": "markdown",
                        "element_id": "thinking_status",
                        "content": f"**{initial_status}**",
                    },
                    {
                        "tag": "note",
                        "element_id": "thinking_time",
                        "elements": [{"tag": "plain_text", "content": "⏱️ 0秒"}],
                    },
                ]
            },
        }

        message_id = await api_client.async_send_streaming_card(
            receive_id=receive_id,
            card=initial_card,
            receive_id_type=receive_type,
        )

        session = ThinkingSession(
            message_id=message_id,
            conversation_id=conversation_id,
            chat_id=receive_id,
            receive_type=receive_type,
            api_client=api_client,
            current_status=initial_status,
        )

        cls._sessions[conversation_id] = session

        # Start auto-update task for elapsed time
        session._update_task = asyncio.create_task(
            cls._auto_update(session)
        )

        return session

    @classmethod
    async def _auto_update(cls, session: ThinkingSession) -> None:
        """Auto-update elapsed time every 5 seconds."""
        while True:
            try:
                await asyncio.sleep(5)
                elapsed = int(asyncio.get_event_loop().time() - session.start_time)
                await session.update(
                    session.current_status,
                    details=f"",  # Keep existing details
                )
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.debug("Auto-update error: %s", err)

    @classmethod
    def get_session(cls, conversation_id: str) -> ThinkingSession | None:
        """Get active thinking session for conversation."""
        return cls._sessions.get(conversation_id)

    @classmethod
    async def update_status(
        cls,
        conversation_id: str,
        status: str,
        details: str = "",
    ) -> bool:
        """Update thinking status for a conversation.

        Args:
            conversation_id: The conversation ID.
            status: New status message.
            details: Optional details.

        Returns:
            True if updated, False if no active session.
        """
        session = cls._sessions.get(conversation_id)
        if not session:
            return False

        await session.update(status, details)
        return True

    @classmethod
    async def finish(
        cls,
        conversation_id: str,
        final_result: str | None = None,
    ) -> bool:
        """Finish thinking session.

        Args:
            conversation_id: The conversation ID.
            final_result: Optional final result to display.

        Returns:
            True if finished, False if no active session.
        """
        session = cls._sessions.pop(conversation_id, None)
        if not session:
            return False

        await session.finish(final_result)
        return True


# ---- Status message builders ----

class ThinkingStatus:
    """Predefined thinking status messages with icons."""

    ANALYZING = "🧠 正在分析问题..."
    SEARCHING = "🔍 正在搜索信息..."
    CALLING_TOOLS = "🛠️ 正在调用工具..."
    PROCESSING = "⚙️ 正在处理数据..."
    REASONING = "💭 正在推理..."
    GENERATING = "✍️ 正在生成回复..."
    VERIFYING = "✓ 正在验证结果..."
    COMPLETED = "✅ 思考完成"

    @staticmethod
    def custom(icon: str, message: str) -> str:
        """Create custom status with icon."""
        return f"{icon} {message}"
