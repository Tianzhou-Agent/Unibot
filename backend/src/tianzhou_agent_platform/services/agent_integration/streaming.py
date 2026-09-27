"""Native streams → application events (plan §6.8).

The runtime emits native stream data; the API layer formats the SSE envelope.
Filter internal generations through explicit run metadata and emit one
terminal result/error.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessageChunk, BaseMessage

EventSink = Callable[[dict[str, Any]], Awaitable[None]]

# Public SSE event types preserved from the existing protocol.
EVENT_MESSAGE_DELTA = "message.delta"
EVENT_MESSAGE_COMPLETED = "message.completed"
EVENT_APPROVAL_REQUIRED = "approval.required"
EVENT_ERROR = "error"


def is_internal_generation(metadata: dict[str, Any] | None) -> bool:
    """Filter summarization / middleware internal model generations."""
    meta = metadata or {}
    if meta.get("lc_source") == "summarization":
        return True
    if metadata is None:
        return False
    # Native callbacks tag middleware-internal calls.
    return bool(meta.get("internal") or meta.get("tags") and "internal" in (meta.get("tags") or []))


async def translate_native_stream(
    stream: AsyncIterator[Any],
    *,
    event_sink: EventSink | None = None,
) -> str:
    """Translate a native message/update stream into application events.

    Returns the full assistant text. Emits at most one text sequence and one
    terminal outcome. Internal generations never leak as user-visible deltas.
    """
    parts: list[str] = []
    async for chunk in stream:
        text, metadata = _extract_delta(chunk)
        if text is None:
            continue
        if is_internal_generation(metadata):
            continue
        if text:
            parts.append(text)
            if event_sink is not None:
                await event_sink({"type": EVENT_MESSAGE_DELTA, "delta": text})
    return "".join(parts)


def _extract_delta(chunk: Any) -> tuple[str | None, dict[str, Any] | None]:
    """Pull a text delta and metadata from a native stream chunk."""
    # stream_mode="messages" yields (message_chunk, metadata)
    if isinstance(chunk, tuple) and len(chunk) == 2:
        message, metadata = chunk
        if isinstance(message, AIMessageChunk):
            content = message.content
            text = content if isinstance(content, str) else _blocks_to_text(content)
            return text, metadata if isinstance(metadata, dict) else {}
        if isinstance(message, BaseMessage):
            content = message.content
            text = content if isinstance(content, str) else _blocks_to_text(content)
            return text, metadata if isinstance(metadata, dict) else {}
        return None, metadata if isinstance(metadata, dict) else {}
    if isinstance(chunk, AIMessageChunk):
        content = chunk.content
        text = content if isinstance(content, str) else _blocks_to_text(content)
        return text, {}
    return None, None


def _blocks_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(b) if isinstance(b, str) else str(b.get("text", "")) for b in content if isinstance(b, (str, dict)))
    return str(content or "")


async def emit_terminal(
    event_sink: EventSink,
    *,
    status: str,
    response: Any | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    """Exactly one terminal result or error per turn."""
    if error is not None:
        await event_sink({"type": EVENT_ERROR, "error": error})
        return
    payload = response.model_dump(mode="json") if hasattr(response, "model_dump") else response
    await event_sink({"type": EVENT_MESSAGE_COMPLETED, "response": payload, "status": status})
