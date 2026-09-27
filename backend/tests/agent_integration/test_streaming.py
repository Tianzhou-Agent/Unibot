"""Phase 5: native callbacks, stream translation and generation filtering."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessageChunk, HumanMessage

from tianzhou_agent_platform.observability.callbacks import ObservationCallbackHandler
from tianzhou_agent_platform.observability.events import emit_repository_event
from tianzhou_agent_platform.services.agent_integration.builder import build_chat_agent
from tianzhou_agent_platform.services.agent_integration.streaming import (
    EVENT_MESSAGE_COMPLETED,
    EVENT_MESSAGE_DELTA,
    emit_terminal,
    is_internal_generation,
    translate_native_stream,
)
from tests.support.fake_chat_model import ScriptedChatModel, assistant, tool_calling


class FakeSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def on_model_start(self, **kwargs: Any) -> None:
        self.events.append(("model_start", kwargs))

    async def on_model_end(self, **kwargs: Any) -> None:
        self.events.append(("model_end", kwargs))

    async def on_model_error(self, **kwargs: Any) -> None:
        self.events.append(("model_error", kwargs))

    async def on_tool_start(self, **kwargs: Any) -> None:
        self.events.append(("tool_start", kwargs))

    async def on_tool_end(self, **kwargs: Any) -> None:
        self.events.append(("tool_end", kwargs))

    async def on_tool_error(self, **kwargs: Any) -> None:
        self.events.append(("tool_error", kwargs))

    async def on_token(self, **kwargs: Any) -> None:
        self.events.append(("token", kwargs))

    async def on_retry(self, **kwargs: Any) -> None:
        self.events.append(("retry", kwargs))

    async def on_repository_event(self, **kwargs: Any) -> None:
        self.events.append(("repository", kwargs))


def test_internal_generation_filter() -> None:
    assert is_internal_generation({"lc_source": "summarization"})
    assert is_internal_generation({"internal": True})
    assert not is_internal_generation({"lc_source": "agent"})
    assert not is_internal_generation(None)


async def test_translate_native_stream_emits_deltas() -> None:
    events: list[dict] = []

    async def sink(event: dict) -> None:
        events.append(event)

    async def fake_stream():
        yield (AIMessageChunk(content="Hel"), {"langgraph_node": "model"})
        yield (AIMessageChunk(content="lo"), {"langgraph_node": "model"})

    text = await translate_native_stream(fake_stream(), event_sink=sink)
    assert text == "Hello"
    assert events == [
        {"type": EVENT_MESSAGE_DELTA, "delta": "Hel"},
        {"type": EVENT_MESSAGE_DELTA, "delta": "lo"},
    ]


async def test_translate_skips_internal_generations() -> None:
    events: list[dict] = []

    async def sink(event: dict) -> None:
        events.append(event)

    async def fake_stream():
        yield (AIMessageChunk(content="SECRET"), {"lc_source": "summarization"})
        yield (AIMessageChunk(content="public"), {"langgraph_node": "model"})

    text = await translate_native_stream(fake_stream(), event_sink=sink)
    assert text == "public"
    assert all(e["delta"] != "SECRET" for e in events)


async def test_emit_terminal_single_outcome() -> None:
    events: list[dict] = []

    async def sink(event: dict) -> None:
        events.append(event)

    class Resp:
        def model_dump(self, mode: str = "json"):
            return {"content": "done", "status": "completed"}

    await emit_terminal(sink, status="completed", response=Resp())
    assert len(events) == 1
    assert events[0]["type"] == EVENT_MESSAGE_COMPLETED


async def test_callback_handler_forwards_to_sink() -> None:
    sink = FakeSink()
    handler = ObservationCallbackHandler(sink=sink)
    await handler.on_chat_model_start({}, [[]])
    await handler.on_llm_end(None)
    await handler.on_tool_start({"name": "t"}, "{}")
    await handler.on_tool_end("ok")
    kinds = [k for k, _ in sink.events]
    assert "model_start" in kinds
    assert "model_end" in kinds
    assert "tool_start" in kinds
    assert "tool_end" in kinds


async def test_builder_attaches_observation_callback() -> None:
    sink = FakeSink()
    model = ScriptedChatModel(responses=[assistant("hi")])
    agent = build_chat_agent(model=model, observation_sink=sink, use_model_call_limit=False)
    await agent.ainvoke({"messages": [HumanMessage(content="go")]})
    kinds = [k for k, _ in sink.events]
    assert "model_start" in kinds


async def test_repository_event_is_explicit() -> None:
    sink = FakeSink()
    await emit_repository_event(
        sink,
        kind="archive.append",
        status="completed",
        target_type="conversation",
        target_id="c1",
    )
    assert sink.events[-1][0] == "repository"
    # Failure isolation: missing handler is a no-op.
    await emit_repository_event(None, kind="x", status="y")
