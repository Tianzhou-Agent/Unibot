"""Native stream relay to public application events."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessageChunk, ToolMessage

from tianzhou_agent_platform.services.agent_integration.streaming import StreamRelay


async def _stream(*items: tuple[Any, dict[str, Any]]) -> AsyncIterator[tuple[Any, dict[str, Any]]]:
    for item in items:
        yield item


async def test_only_the_answering_model_text_is_streamed() -> None:
    events: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    relay = StreamRelay(sink)
    await relay.relay(
        _stream(
            (AIMessageChunk(content="Hel", id="run-1"), {"langgraph_node": "model"}),
            (AIMessageChunk(content="summary text", id="run-2"), {"langgraph_node": "TurnSummarizationMiddleware.before_model"}),
            (ToolMessage(content="tool output", tool_call_id="c1"), {"langgraph_node": "tools"}),
            (AIMessageChunk(content="lo", id="run-1"), {"langgraph_node": "model"}),
        )
    )

    assert events == [{"type": "message.delta", "delta": "Hel"}, {"type": "message.delta", "delta": "lo"}]
    assert relay.streamed == "Hello"


async def test_final_answer_is_sent_once() -> None:
    events: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    streamed = StreamRelay(sink)
    await streamed.relay(_stream((AIMessageChunk(content="Partial", id="run-1"), {"langgraph_node": "model"})))
    await streamed.finish("Partial\n\n[notice]")
    unstreamed = StreamRelay(sink)
    await unstreamed.finish("Direct reply")

    assert [event["delta"] for event in events] == ["Partial", "\n\n[notice]", "Direct reply"]
