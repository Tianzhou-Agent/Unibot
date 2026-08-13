from __future__ import annotations

import asyncio

from tianzhou_agent_platform.core.observation_context import (
    ObservationContext,
    bind_observation_context,
    current_observation_context,
    is_observation_suppressed,
    suppress_observation,
)


async def test_observation_context_is_isolated_per_async_task() -> None:
    async def read_context(trace_id: str) -> str:
        context = ObservationContext(trace_id, None, "user", "tenant")
        with bind_observation_context(context):
            await asyncio.sleep(0)
            assert current_observation_context() is context
            return context.legacy_trace_id

    assert await asyncio.gather(read_context("trace_a"), read_context("trace_b")) == [
        "trace_a",
        "trace_b",
    ]
    assert current_observation_context() is None


def test_observation_suppression_is_nested_and_restored() -> None:
    assert not is_observation_suppressed()
    with suppress_observation():
        assert is_observation_suppressed()
        with suppress_observation():
            assert is_observation_suppressed()
        assert is_observation_suppressed()
    assert not is_observation_suppressed()
