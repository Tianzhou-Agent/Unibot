"""Conversation archive ↔ native messages.

The checkpointed agent state is the working memory; the repository keeps the complete original transcript.
Messages are archived under their native message IDs, so archiving is idempotent; the runner hands the archiver
to the runtime's ``TranscriptHookMiddleware`` so summarization never removes an original record.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.core.agent_runtime.middleware.summarization import is_summary_message
from tianzhou_agent_platform.conversations.models import Message
from tianzhou_agent_platform.model_providers.factory import REASONING_KEY
from tianzhou_agent_platform.services.agent_integration.capabilities import provider_messages_for_scope


def native_to_wire(message: Any) -> dict[str, Any] | None:
    """OpenAI chat-completions shape of a native message (used for archive records and trace payloads)."""
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": _text(message)}
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": _text(message)}
    if isinstance(message, ToolMessage):
        return {
            "role": "tool",
            "content": _text(message),
            "tool_call_id": message.tool_call_id,
            "name": message.name,
        }
    if isinstance(message, AIMessage):
        item: dict[str, Any] = {"role": "assistant", "content": _text(message)}
        tool_calls = [
            {
                "id": call.get("id"),
                "type": "function",
                "function": {
                    "name": call.get("name"),
                    "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
                },
            }
            for call in message.tool_calls or []
        ]
        # Calls whose arguments were not valid JSON keep the provider's raw arguments; they are answered too.
        tool_calls += [
            {
                "id": call.get("id"),
                "type": "function",
                "function": {"name": call.get("name"), "arguments": call.get("args") or ""},
            }
            for call in message.invalid_tool_calls or []
        ]
        if tool_calls:
            item["tool_calls"] = tool_calls
        return item
    return None


def wire_to_native(item: dict[str, Any], *, message_id: str | None = None) -> BaseMessage:
    role = item.get("role")
    content = item.get("content") or ""
    if role == "system":
        return SystemMessage(content=content, id=message_id)
    if role == "user":
        return HumanMessage(content=content, id=message_id)
    if role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=str(item.get("tool_call_id") or ""),
            name=str(item.get("name") or ""),
            id=message_id,
        )
    tool_calls: list[dict[str, Any]] = []
    invalid_tool_calls: list[dict[str, Any]] = []
    for call in item.get("tool_calls") or []:
        function = call.get("function") or {}
        name = function.get("name") or call.get("name")
        arguments = function.get("arguments") or {}
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as exc:
                # Kept as the provider returned it, the way LangChain represents unparseable arguments.
                invalid_tool_calls.append({"name": name, "args": arguments, "id": call.get("id"), "error": str(exc)})
                continue
        tool_calls.append({"name": name, "args": arguments, "id": call.get("id")})
    return AIMessage(content=content, tool_calls=tool_calls, invalid_tool_calls=invalid_tool_calls, id=message_id)


def archive_to_native(message: Message) -> BaseMessage:
    """Rebuild working memory from a durable archive record (same ID, so it is never archived twice)."""
    return wire_to_native(message.provider_message(), message_id=message.id)


def project_scope_history(messages: list[Any], visible_names: set[str]) -> list[Any]:
    """Model-request view of the transcript: calls outside the visible scope become non-executable history."""
    wire = [record for message in messages if (record := native_to_wire(message)) is not None]
    return [wire_to_native(item) for item in provider_messages_for_scope(wire, active_function_names=visible_names)]


def dangling_tool_closures(messages: list[BaseMessage], content: str) -> list[ToolMessage]:
    """Close tool calls of the last model response that never produced a result (e.g. an abandoned approval)."""
    last_ai = next((message for message in reversed(messages) if isinstance(message, AIMessage)), None)
    if last_ai is None:
        return []
    answered = {message.tool_call_id for message in messages if isinstance(message, ToolMessage)}
    return [
        ToolMessage(
            content=content,
            tool_call_id=str(call.get("id")),
            name=str(call.get("name") or "unknown"),
            id=f"msg_{uuid4().hex}",
        )
        for call in [*last_ai.tool_calls, *last_ai.invalid_tool_calls]
        if call.get("id") and call.get("id") not in answered
    ]


class TranscriptArchiver:
    """Appends working-state messages to the conversation archive exactly once, by message ID."""

    def __init__(self, conversations: Any, *, conversation_id: str, trace_id: str, archived_ids: Iterable[str]) -> None:
        self._conversations = conversations
        self._conversation_id = conversation_id
        self._trace_id = trace_id
        self._archived = set(archived_ids)
        self.appended: list[Message] = []

    def is_archived(self, message_id: str | None) -> bool:
        return message_id in self._archived

    async def archive(
        self,
        messages: Iterable[Any],
        *,
        widgets: dict[str, list[WidgetDefinition]] | None = None,
    ) -> list[Message]:
        records: list[Message] = []
        for message in messages:
            if message.id is None or message.id in self._archived or is_summary_message(message):
                continue
            wire = native_to_wire(message)
            if wire is None:
                continue
            attached = (widgets or {}).get(message.id) or []
            reasoning = message.additional_kwargs.get(REASONING_KEY) if isinstance(message, AIMessage) else None
            records.append(
                Message(
                    id=message.id,
                    role=wire["role"],
                    content=wire.get("content") or "",
                    reasoning=reasoning if isinstance(reasoning, str) and reasoning else None,
                    tool_calls=wire.get("tool_calls"),
                    tool_call_id=wire.get("tool_call_id"),
                    name=wire.get("name"),
                    widgets=attached,
                    content_type="tool" if wire["role"] == "tool" else "widget" if attached else "text",
                    trace_id=self._trace_id,
                )
            )
        if records:
            # Archive writes are correctness-critical: a failure fails the run instead of being swallowed.
            await self._conversations.append_messages_idempotent(self._conversation_id, records)
            self._archived.update(record.id for record in records)
            self.appended.extend(records)
        return records


def _text(message: BaseMessage) -> str:
    content = message.content
    return content if isinstance(content, str) else str(content or "")
