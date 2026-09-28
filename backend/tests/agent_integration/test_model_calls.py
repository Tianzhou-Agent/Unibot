"""Model-call observation of the native loop (ported from the retired ObservedLLMClient assertions)."""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage

from tianzhou_agent_platform.core.agent import _native_to_wire
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.run_events import RunEventPublisher
from tianzhou_agent_platform.services.agent_integration.model_calls import (
    ModelCallRecorder,
    record_standalone_call,
)


class RecordingObservability:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.finished: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.events: list[dict[str, Any]] = []
        self.timeline: list[str] = []

    async def start_span(self, trace_id: str, **kwargs: Any) -> None:
        self.timeline.append("span.started")
        self.started.append({"trace_id": trace_id, **kwargs})

    async def finish_span(self, *args: Any, **kwargs: Any) -> None:
        self.timeline.append("span.finished")
        self.finished.append((args, kwargs))

    async def record_event(self, trace_id: str, **event: Any) -> None:
        self.timeline.append(event["kind"])
        self.events.append(event)


async def _recorder(observability: RecordingObservability, calls: list[Any]) -> ModelCallRecorder:
    events = RunEventPublisher(observability)
    await events.start(trace_id="trace_a", conversation_id="conv", user_id="user", tenant_id="tenant", input_data={})

    async def sink(call: Any) -> None:
        calls.append(call)

    return ModelCallRecorder(
        events=events,
        trace_id="trace_a",
        conversation_id="conv",
        model="test-model",
        endpoint="https://model.invalid/v1/chat/completions",
        to_wire=_native_to_wire,
        visible_capabilities=lambda: {},
        call_sink=sink,
    )


def _request() -> ModelRequest:
    return ModelRequest(
        model=None,  # type: ignore[arg-type]
        messages=[HumanMessage(content="go")],
        system_message=None,
        tool_choice=None,
        tools=[],
        response_format=None,
        state={},  # type: ignore[arg-type]
        runtime=None,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_model_call_owns_span_events_and_llm_call_record() -> None:
    observability = RecordingObservability()
    calls: list[Any] = []
    recorder = await _recorder(observability, calls)

    async def handler(_: ModelRequest) -> ModelResponse:
        usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
        return ModelResponse(result=[AIMessage(content="done", usage_metadata=usage)])

    response = await recorder.awrap_model_call(_request(), handler)

    assert response.result[0].content == "done"
    started = observability.started[0]
    assert started["kind"] == "model"
    assert started["parent_span_id"]  # the run's root span
    assert observability.finished[0][0] == ("trace_a", started["span_id"], "completed")
    assert observability.finished[0][1]["attributes"]["input_tokens"] == 10
    assert observability.timeline == ["model.requested", "span.started", "span.finished", "model.completed"]
    assert observability.events[0]["details"] == {
        "iteration": 1,
        "message_count": 1,
        "message_roles": ["user"],
        "capability_ids": [],
        "forced_function": None,
        "streaming": False,
    }
    assert observability.events[1]["details"]["input_tokens"] == 10
    assert [call.status for call in calls] == ["running", "completed"]
    assert {call.span_id for call in calls} == {started["span_id"]}
    assert (recorder.usage.input_tokens, recorder.usage.output_tokens) == (10, 2)


@pytest.mark.asyncio
async def test_model_call_failure_finishes_failed_span_and_reraises() -> None:
    observability = RecordingObservability()
    calls: list[Any] = []
    recorder = await _recorder(observability, calls)

    async def handler(_: ModelRequest) -> ModelResponse:
        raise PlatformError("DEPENDENCY_FAILED", "provider failed", retryable=True)

    with pytest.raises(PlatformError):
        await recorder.awrap_model_call(_request(), handler)

    assert observability.finished[0][0][2] == "failed"
    assert observability.finished[0][1]["error"]["retryable"] is True
    assert observability.timeline == ["model.requested", "span.started", "span.finished", "model.failed"]
    assert observability.events[-1]["details"]["code"] == "DEPENDENCY_FAILED"
    assert calls[-1].status == "failed"


@pytest.mark.asyncio
async def test_compression_call_record_is_linked_to_the_compression_span() -> None:
    calls: list[Any] = []

    async def sink(call: Any) -> None:
        calls.append(call)

    usage = await record_standalone_call(
        sink,
        trace_id="trace_a",
        span_id="span_compress",
        context_type="compression",
        context_id="conv",
        model="test-model",
        endpoint="https://model.invalid/v1/chat/completions",
        messages=[{"role": "user", "content": "summarize"}],
        response=AIMessage(content="summary", usage_metadata={"input_tokens": 7, "output_tokens": 3, "total_tokens": 10}),
        duration_ms=1.0,
    )

    assert usage == (7, 3)
    assert calls[0].span_id == "span_compress"
    assert calls[0].context_type == "compression"
    assert calls[0].status == "completed"
