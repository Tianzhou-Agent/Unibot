"""Archive ↔ native message conversion, legacy import and transcript hooks.

Convert only at API/archive boundaries. Preserve structured content, invalid
tool-call information, call IDs, finish reasons and usage metadata.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    AnyMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from tianzhou_agent_platform.conversations.models import Message


def archive_to_native(message: Message) -> BaseMessage:
    """Convert a durable archive record to a native execution message."""
    if message.role == "user":
        return HumanMessage(content=message.content, id=message.id)
    if message.role == "system":
        return SystemMessage(content=message.content, id=message.id)
    if message.role == "tool":
        return ToolMessage(
            content=message.content,
            tool_call_id=message.tool_call_id or "",
            name=message.name or "",
            id=message.id,
        )
    tool_calls = message.tool_calls or []
    return AIMessage(content=message.content, tool_calls=tool_calls or [], id=message.id)


def native_to_archive(message: BaseMessage, *, trace_id: str | None = None) -> Message:
    """Convert a native message to a durable archive record with a stable ID."""
    message_id = getattr(message, "id", None) or f"msg_{uuid4().hex}"
    if isinstance(message, HumanMessage):
        return Message(id=message_id, role="user", content=_text(message), trace_id=trace_id)
    if isinstance(message, SystemMessage):
        return Message(id=message_id, role="system", content=_text(message), trace_id=trace_id)
    if isinstance(message, ToolMessage):
        return Message(
            id=message_id,
            role="tool",
            content=_text(message),
            tool_call_id=message.tool_call_id,
            name=message.name,
            trace_id=trace_id,
        )
    tool_calls = getattr(message, "tool_calls", None) or []
    return Message(
        id=message_id,
        role="assistant",
        content=_text(message),
        tool_calls=list(tool_calls) or None,
        trace_id=trace_id,
    )


def _text(message: BaseMessage) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return str(content or "")


def legacy_history_to_native(messages: list[Message]) -> list[BaseMessage]:
    """One-time import of active history for a legacy conversation."""
    return [archive_to_native(item) for item in messages]


def new_turn_inputs(user_text: str, *, message_id: str | None = None) -> list[BaseMessage]:
    """Submit only new input after initialization (never the full transcript)."""
    return [HumanMessage(content=user_text, id=message_id or f"msg_{uuid4().hex}")]


async def archive_before_compaction(
    conversation_service: Any,
    *,
    conversation_id: str,
    messages: list[BaseMessage],
    trace_id: str | None = None,
) -> None:
    """Persist original model/tool outputs before summarization can remove them.

    Archive writes are correctness-critical and must not live in observation
    callbacks that intentionally swallow failures.
    """
    records = [native_to_archive(item, trace_id=trace_id) for item in messages]
    await conversation_service.append_messages_idempotent(conversation_id, records)
