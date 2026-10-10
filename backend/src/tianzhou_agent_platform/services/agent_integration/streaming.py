"""Native agent streams → the public ``message.delta`` and ``reasoning.delta`` application events (plan §6.8).

Only the answering model's text and its thinking are public: tool output, middleware-internal generations (e.g.
summaries) and other nodes are never streamed. The API layer formats the SSE envelope.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from langchain_core.messages import AIMessageChunk

from tianzhou_agent_platform.model_providers.factory import REASONING_KEY

EventSink = Callable[[dict[str, Any]], Awaitable[None]]

AGENT_MODEL_NODE = "model"


class StreamRelay:
    """Forwards text chunks of the agent's model node and remembers what the client already received."""

    def __init__(self, event_sink: EventSink) -> None:
        self._event_sink = event_sink
        self._message_id: str | None = None
        # Text of the latest model call already sent, so the final answer is not sent twice.
        self.streamed = ""

    async def relay(self, stream: AsyncIterator[tuple[Any, dict[str, Any]]]) -> None:
        """Consume an ``astream(stream_mode="messages")`` iterator."""
        async for chunk, metadata in stream:
            if metadata.get("langgraph_node") != AGENT_MODEL_NODE or not isinstance(chunk, AIMessageChunk):
                continue
            if chunk.id != self._message_id:
                self._message_id, self.streamed = chunk.id, ""
            reasoning = chunk.additional_kwargs.get(REASONING_KEY)
            if isinstance(reasoning, str) and reasoning:
                await self._event_sink({"type": "reasoning.delta", "delta": reasoning})
            text = chunk.content if isinstance(chunk.content, str) else ""
            if text:
                self.streamed += text
                await self._event_sink({"type": "message.delta", "delta": text})

    async def finish(self, final_content: str) -> None:
        """One public text sequence: send what the model stream did not already deliver (direct replies,
        budget refusals, or a notice a guard appended to streamed text)."""
        if not final_content:
            return
        if not self.streamed:
            await self._event_sink({"type": "message.delta", "delta": final_content})
        elif final_content.startswith(self.streamed) and len(final_content) > len(self.streamed):
            await self._event_sink({"type": "message.delta", "delta": final_content[len(self.streamed) :]})
