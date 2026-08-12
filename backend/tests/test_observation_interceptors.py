from __future__ import annotations

from typing import Any

import pytest

from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.llm import LLMResult
from tianzhou_agent_platform.core.observation_context import (
    ObservationContext,
    bind_observation_context,
)
from tianzhou_agent_platform.core.observation_interceptors import (
    ObservedLLMClient,
)


class RecordingObservability:
    def __init__(self) -> None:
        self.started: list[dict[str, Any]] = []
        self.finished: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.timeline: list[str] = []

    async def start_span(self, trace_id: str, **kwargs: Any) -> None:
        self.timeline.append("span.started")
        self.started.append({"trace_id": trace_id, **kwargs})

    async def finish_span(self, *args: Any, **kwargs: Any) -> None:
        self.timeline.append("span.finished")
        self.finished.append((args, kwargs))

    async def record_event(self, trace_id: str, **event: Any) -> None:
        self.timeline.append(event["kind"])
        self.events.append((trace_id, event))


class StubLLM:
    def __init__(self, result: LLMResult | Exception) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> LLMResult:
        self.calls.append(kwargs)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.asyncio
async def test_observed_llm_client_owns_model_span_lifecycle() -> None:
    delegate = StubLLM(
        LLMResult(
            message={"role": "assistant", "content": "done"},
            input_tokens=10,
            output_tokens=2,
        )
    )
    observability = RecordingObservability()
    client = ObservedLLMClient(delegate, observability)  # type: ignore[arg-type]
    context = ObservationContext("trace_a", "conv", "user", "tenant", "span_root")

    with bind_observation_context(context):
        result = await client.complete(
            messages=[{"role": "user", "content": "go"}],
            tools=[],
            trace_id="trace_a",
            context_type="conversation",
            context_id="conv",
        )

    assert result.message["content"] == "done"
    assert len(observability.started) == 1
    started = observability.started[0]
    assert started["parent_span_id"] == "span_root"
    assert started["kind"] == "model"
    generated_span_id = started["span_id"]
    assert delegate.calls[0]["span_id"] == generated_span_id
    assert observability.finished[0][0] == ("trace_a", generated_span_id, "completed")
    assert observability.finished[0][1]["attributes"]["input_tokens"] == 10
    assert observability.timeline == [
        "model.requested",
        "span.started",
        "span.finished",
        "model.completed",
    ]
    requested = observability.events[0][1]
    assert requested["details"] == {
        "iteration": 1,
        "message_count": 1,
        "message_roles": ["user"],
        "capability_ids": [],
        "forced_function": None,
        "streaming": False,
    }
    completed = observability.events[1][1]
    assert completed["details"]["iteration"] == 1
    assert completed["details"]["input_tokens"] == 10


@pytest.mark.asyncio
async def test_observed_llm_client_finishes_failed_span_and_reraises() -> None:
    failure = PlatformError("DEPENDENCY_FAILED", "provider failed", retryable=True)
    delegate = StubLLM(failure)
    observability = RecordingObservability()
    client = ObservedLLMClient(delegate, observability)  # type: ignore[arg-type]
    context = ObservationContext("trace_a", "conv", "user", "tenant", "span_root")

    with bind_observation_context(context), pytest.raises(PlatformError):
        await client.complete(
            messages=[],
            tools=[],
            trace_id="trace_a",
            context_type="conversation",
        )

    assert observability.finished[0][0][2] == "failed"
    assert observability.finished[0][1]["error"]["retryable"] is True
    assert observability.timeline == [
        "model.requested",
        "span.started",
        "span.finished",
        "model.failed",
    ]
    assert observability.events[-1][1]["details"]["code"] == "DEPENDENCY_FAILED"


@pytest.mark.asyncio
async def test_observed_llm_client_nests_model_call_under_compression_span() -> None:
    delegate = StubLLM(LLMResult(message={"role": "assistant", "content": "summary"}))
    observability = RecordingObservability()
    client = ObservedLLMClient(delegate, observability)  # type: ignore[arg-type]
    context = ObservationContext("trace_a", "conv", "user", "tenant", "span_root")

    with bind_observation_context(context):
        await client.complete(
            messages=[],
            tools=[],
            trace_id="trace_a",
            context_type="compression",
        )

    assert observability.started[0]["parent_span_id"] == "span_root"
    assert observability.started[0]["attributes"]["context_type"] == "compression"
    assert delegate.calls[0]["span_id"] == observability.started[0]["span_id"]
    assert observability.finished[0][0][2] == "completed"
