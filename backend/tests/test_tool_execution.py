"""Unit tests for the pure tool-execution helpers in core/tool_execution.py."""

from __future__ import annotations

import asyncio

import pytest

from tianzhou_agent_platform.core.capability import Capability
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.tool_execution import (
    call_signature,
    collect_approval_required,
    decode_arguments,
    tool_output_message,
    truncate_tool_output,
    validate_call_arguments,
)


def _capability(
    *,
    capability_id: str = "com.example.risky",
    requires_confirmation: bool = True,
    schema: dict | None = None,
) -> Capability:
    return Capability(
        kind="builtin",
        capability_id=capability_id,
        function_name=capability_id,
        display_name="Risky tool",
        description="Sends an email.",
        input_schema=schema or {"type": "object", "properties": {"to": {"type": "string"}}},
        requires_confirmation=requires_confirmation,
        value=capability_id,
    )


def _tool_call(name: str, arguments: str, call_id: str = "call_1") -> dict:
    return {"id": call_id, "function": {"name": name, "arguments": arguments}}


def test_collect_approval_required_picks_unapproved_risky_calls() -> None:
    capability = _capability()
    calls = [_tool_call("com.example.risky", '{"to": "a@example.com"}')]
    risky, names = collect_approval_required(calls, {"com.example.risky": capability}, approved=set())
    assert len(risky) == 1
    assert names == ["Risky tool"]


def test_collect_approval_required_skips_approved_calls() -> None:
    capability = _capability()
    calls = [_tool_call("com.example.risky", '{"to": "a@example.com"}', call_id="call_7")]
    risky, _ = collect_approval_required(calls, {"com.example.risky": capability}, approved={"call_7"})
    assert risky == []


def test_collect_approval_required_skips_safe_and_unknown_calls() -> None:
    safe = _capability(capability_id="com.example.safe", requires_confirmation=False)
    calls = [
        _tool_call("com.example.safe", "{}"),
        _tool_call("com.example.unknown", "{}"),
    ]
    risky, _ = collect_approval_required(
        calls,
        {"com.example.safe": safe, "com.example.risky": _capability()},
        approved=set(),
    )
    assert risky == []


def test_collect_approval_required_skips_invalid_arguments() -> None:
    capability = _capability(schema={"type": "object", "required": ["to"]})
    calls = [_tool_call("com.example.risky", '{"missing": true}')]
    risky, _ = collect_approval_required(calls, {"com.example.risky": capability}, approved=set())
    assert risky == []


def test_decode_arguments_accepts_dict_and_rejects_other_types() -> None:
    assert decode_arguments('{"a": 1}') == {"a": 1}
    assert decode_arguments({"a": 1}) == {"a": 1}
    with pytest.raises(ValueError, match="must decode to an object"):
        decode_arguments("[1, 2]")
    with pytest.raises(ValueError, match="must decode to an object"):
        decode_arguments(b"{}")


def test_call_signature_is_stable_and_key_sensitive() -> None:
    assert call_signature("tool", {"b": 1, "a": 2}) == call_signature("tool", {"a": 2, "b": 1})
    assert call_signature("tool", {"a": 1}) != call_signature("tool", {"a": 2})
    assert call_signature("tool", {"a": 1}) != call_signature("other", {"a": 1})


def test_validate_call_arguments_rejects_invalid_input() -> None:
    capability = _capability(schema={"type": "object", "required": ["to"]})
    with pytest.raises(PlatformError):
        validate_call_arguments(capability, {"missing": True})


def test_truncate_tool_output_keeps_small_and_truncates_large() -> None:
    small = "x" * 100
    assert truncate_tool_output(small) == small
    large = truncate_tool_output("x" * 60_000)
    assert len(large) < 60_000
    assert large.endswith("[tool output truncated]")


def test_tool_output_message_shape() -> None:
    message = tool_output_message("com.example.tool", "call_9", "ok")
    assert message == {"role": "tool", "name": "com.example.tool", "tool_call_id": "call_9", "content": "ok"}


class _FakeObservability:
    async def start_span(self, *args, **kwargs) -> None:
        pass

    async def finish_span(self, *args, **kwargs) -> None:
        pass

    async def record_event(self, *args, **kwargs) -> None:
        pass


def _tool_executor() -> tuple:
    from tianzhou_agent_platform.core.repository import InMemoryRepository
    from tianzhou_agent_platform.core.tool_executor import ToolExecutor

    events: list[dict] = []
    errors: list[dict] = []

    async def emit(state, event) -> None:
        events.append(event)

    async def append_tool_error(state, messages, **kwargs) -> None:
        errors.append(kwargs)

    async def activate_builtin(state, **kwargs) -> dict:
        return state["capabilities"]

    async def activate_aina(state, **kwargs) -> dict:
        return state["capabilities"]

    executor = ToolExecutor(
        repository=InMemoryRepository(),
        observability=_FakeObservability(),
        gateway=None,
        document_service=None,
        document_edit_task_service=None,
        sandbox_service=None,
        emit=emit,
        append_tool_error=append_tool_error,
        activate_builtin_aina_scope=activate_builtin,
        activate_aina_model_scope=activate_aina,
    )
    return executor, events, errors


def test_tool_executor_approval_required_path() -> None:
    from tianzhou_agent_platform.core.tool_executor import AgentState

    executor, events, _ = _tool_executor()
    capability = _capability()
    state: AgentState = {
        "messages": [],
        "capabilities": {"com.example.risky": capability},
        "trace_id": "trace_1",
        "root_span_id": "root_1",
        "conversation_id": "conv_1",
        "user_id": "user_1",
        "tenant_id": "default",
        "iterations": 0,
        "max_iterations": 3,
    }

    async def run() -> AgentState:
        return await executor.execute(state, tool_calls=[_tool_call("com.example.risky", "{}")])

    result = asyncio.run(run())
    assert result["final_status"] == "approval_required"
    assert result["approval"] is not None
    assert result["approval"].capability_names == ["Risky tool"]
    assert any(event.get("type") == "approval.required" for event in events)


def test_tool_executor_rejects_duplicate_call() -> None:
    from tianzhou_agent_platform.core.tool_executor import AgentState

    executor, _, errors = _tool_executor()
    safe = _capability(capability_id="com.example.safe", requires_confirmation=False)
    state: AgentState = {
        "messages": [],
        "capabilities": {"com.example.safe": safe},
        "trace_id": "trace_2",
        "root_span_id": "root_2",
        "conversation_id": "conv_2",
        "user_id": "user_2",
        "tenant_id": "default",
        "iterations": 0,
        "max_iterations": 3,
    }
    calls = [_tool_call("com.example.safe", '{"to": "a@example.com"}', call_id="call_a")] * 2

    async def run() -> AgentState:
        return await executor.execute(state, tool_calls=calls)

    result = asyncio.run(run())
    conflict_errors = [item for item in errors if item["code"] == "CONFLICT"]
    assert len(conflict_errors) == 1
    assert "already attempted" in conflict_errors[0]["message"]
    assert result["call_counts"] is not None
