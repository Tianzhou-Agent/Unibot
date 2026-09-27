"""Phase 3: ordered batches, limits, retries, scope and output guards."""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from tianzhou_agent_platform.core.agent_runtime.middleware.model_policy import (
    OutputGuardMiddleware,
    reject_truncated_call,
    request_input_budget,
)
from tianzhou_agent_platform.core.agent_runtime.middleware.tool_policy import (
    BatchDeduper,
    OrderedBatchMiddleware,
    tool_signature,
)
from tianzhou_agent_platform.services.agent_integration.builder import build_chat_agent
from tianzhou_agent_platform.services.agent_integration.retries import (
    build_model_call_limit,
    build_tool_retry_middleware,
    is_transient_error,
)
from tianzhou_agent_platform.services.agent_integration.scope import (
    claim_scope_activation,
    ensure_execution_scope,
    filter_advertised_tools,
    revalidate_permissions_on_resume,
)
from tests.support.fake_chat_model import ScriptedChatModel, assistant, multi_tool_calling, tool_calling

_LOG: list[str] = []


@tool
def ordered_tool(tag: str, runtime: ToolRuntime) -> str:
    """Append a tag to the shared order log."""
    _LOG.append(tag)
    return tag


@tool
def side_effect_tool(action: str, runtime: ToolRuntime) -> str:
    """Not safe to auto-retry."""
    return f"done:{action}"


@tool
def flaky_readonly(query: str, runtime: ToolRuntime) -> str:
    """Read-only lookup that fails once."""
    attempts = getattr(flaky_readonly, "_attempts", {})
    n = attempts.get(query, 0) + 1
    setattr(flaky_readonly, "_attempts", {**attempts, query: n})
    if n == 1:
        raise TimeoutError("transient")
    return f"ok:{query}"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


async def test_ordered_batch_preserves_tool_side_effect_order() -> None:
    _LOG.clear()
    model = ScriptedChatModel(
        responses=[
            multi_tool_calling(
                [
                    ("ordered_tool", {"tag": "1"}, "c1"),
                    ("ordered_tool", {"tag": "2"}, "c2"),
                    ("ordered_tool", {"tag": "3"}, "c3"),
                    ("ordered_tool", {"tag": "4"}, "c4"),
                ]
            ),
            assistant("done"),
        ]
    )
    agent = build_chat_agent(model=model, tools=[ordered_tool], use_model_call_limit=False)
    await agent.ainvoke({"messages": [HumanMessage(content="order")]})
    # This is the Phase 3 fix for the Phase 0 gap: strict order is required.
    assert _LOG == ["1", "2", "3", "4"]


async def test_ordered_batch_releases_on_failure() -> None:
    _LOG.clear()

    @tool
    def boom(tag: str, runtime: ToolRuntime) -> str:
        """Raise so the batch must still release the successor."""
        raise ValueError("bad")

    @tool
    def after_tool(tag: str, runtime: ToolRuntime) -> str:
        """Runs after the failing tool in the same batch."""
        _LOG.append(tag)
        return tag

    model = ScriptedChatModel(
        responses=[
            multi_tool_calling(
                [
                    ("boom", {"tag": "x"}, "c1"),
                    ("after_tool", {"tag": "y"}, "c2"),
                ]
            ),
            assistant("recovered"),
        ]
    )
    agent = build_chat_agent(
        model=model,
        tools=[boom, after_tool],
        use_model_call_limit=False,
    )
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})
    # Failure of c1 must not deadlock c2.
    assert _LOG == ["y"]
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 2


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def test_tool_signature_stable_and_distinct() -> None:
    a = tool_signature("t", {"x": 1})
    b = tool_signature("t", {"x": 1})
    c = tool_signature("t", {"x": 2})
    assert a == b
    assert a != c


def test_batch_deduper_claims_once() -> None:
    deduper = BatchDeduper()
    assert deduper.claim("t", {"x": 1}) is True
    assert deduper.claim("t", {"x": 1}) is False
    assert deduper.claim("t", {"x": 2}) is True


async def test_same_batch_duplicate_tool_args_are_skipped() -> None:
    _LOG.clear()
    model = ScriptedChatModel(
        responses=[
            multi_tool_calling(
                [
                    ("ordered_tool", {"tag": "same"}, "c1"),
                    ("ordered_tool", {"tag": "same"}, "c2"),
                ]
            ),
            assistant("done"),
        ]
    )
    agent = build_chat_agent(model=model, tools=[ordered_tool], use_model_call_limit=False)
    result = await agent.ainvoke({"messages": [HumanMessage(content="dup")]})
    # Only one physical execution of the same signature.
    assert _LOG == ["same"]
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 2
    assert any("Duplicate" in m.content for m in tool_msgs)


# ---------------------------------------------------------------------------
# Retries
# ---------------------------------------------------------------------------


def test_is_transient_error_platform_and_os() -> None:
    from tianzhou_agent_platform.core.errors import PlatformError

    assert is_transient_error(TimeoutError("t"))
    assert is_transient_error(ConnectionError("c"))
    assert is_transient_error(PlatformError(code="X", message="m", retryable=True))
    assert not is_transient_error(ValueError("v"))
    assert not is_transient_error(PlatformError(code="X", message="m", retryable=False))


async def test_retry_only_eligible_readonly_tools() -> None:
    flaky_readonly._attempts = {}
    model = ScriptedChatModel(
        responses=[
            tool_calling("flaky_readonly", {"query": "q"}, call_id="c1"),
            assistant("after"),
        ]
    )
    agent = build_chat_agent(
        model=model,
        tools=[flaky_readonly, side_effect_tool],
        retry_eligible_tools=["flaky_readonly"],
        retry_max_attempts=2,
        use_model_call_limit=False,
    )
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs[0].content.startswith("ok:")
    assert flaky_readonly._attempts.get("q") == 2


async def test_side_effect_tool_is_not_in_retry_eligible_set() -> None:
    mw = build_tool_retry_middleware(eligible_tools=["flaky_readonly"], max_retries=2)
    # Side-effecting tools must not appear in the native retry list.
    assert "side_effect_tool" not in (mw.tools or [])
    assert "flaky_readonly" in (mw.tools or ["flaky_readonly"])


# ---------------------------------------------------------------------------
# Limits and output guards
# ---------------------------------------------------------------------------


def test_model_call_limit_builder() -> None:
    mw = build_model_call_limit(run_limit=3, thread_limit=10, exit_behavior="end")
    assert mw.run_limit == 3
    assert mw.thread_limit == 10


def test_request_budget_reserves_output() -> None:
    assert request_input_budget(1000, output_reserve=200) == 800
    assert request_input_budget(100, output_reserve=200) == 0


def test_reject_truncated_call() -> None:
    assert reject_truncated_call(finish_reason="length")
    assert reject_truncated_call(finish_reason="max_tokens")
    assert not reject_truncated_call(finish_reason="stop")
    assert not reject_truncated_call(finish_reason=None)


async def test_output_guard_blocks_partial_tool_calls() -> None:
    @tool
    def must_not_run(runtime: ToolRuntime) -> str:
        """Must not execute when the model output was truncated."""
        raise AssertionError("partial tool call executed")

    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="partial text",
                tool_calls=[{"name": "must_not_run", "args": {}, "id": "c1"}],
                response_metadata={"finish_reason": "length"},
            ),
            assistant("recovered"),
        ]
    )
    agent = build_chat_agent(model=model, tools=[must_not_run], use_model_call_limit=False)
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs
    assert "was not executed" in tool_msgs[0].content
    contents = [m.content for m in result["messages"] if isinstance(m, AIMessage)]
    assert any("Incomplete response" in c for c in contents)


# ---------------------------------------------------------------------------
# Scope policy
# ---------------------------------------------------------------------------


def test_filter_advertised_tools_hides_hidden() -> None:
    @tool
    def visible_tool() -> str:
        """Visible."""
        return "v"

    @tool
    def secret_tool() -> str:
        """Hidden."""
        return "s"

    result = filter_advertised_tools(
        [visible_tool, secret_tool],
        hidden_names={"secret_tool"},
    )
    assert [t.name for t in result] == ["visible_tool"]


def test_filter_advertised_tools_allowlist() -> None:
    @tool
    def a_tool() -> str:
        """A."""
        return "a"

    @tool
    def b_tool() -> str:
        """B."""
        return "b"

    result = filter_advertised_tools([a_tool, b_tool], allowed_names={"a_tool"})
    assert [t.name for t in result] == ["a_tool"]


def test_ensure_execution_scope_denies_hidden() -> None:
    from tianzhou_agent_platform.core.errors import PlatformError

    with pytest.raises(PlatformError):
        ensure_execution_scope("secret", hidden_names={"secret"})
    ensure_execution_scope("ok", hidden_names={"secret"})


def test_revalidate_permissions_on_resume() -> None:
    permitted = revalidate_permissions_on_resume(
        ["a", "secret", "b"],
        allowed_names={"a", "b", "secret"},
        hidden_names={"secret"},
    )
    assert permitted == ["a", "b"]


def test_one_scope_activation_per_batch() -> None:
    from tianzhou_agent_platform.core.agent_runtime.middleware.tool_policy import (
        BatchCoordinator,
        _batch_var,
    )

    coord = BatchCoordinator.from_tool_calls("run1", [])
    token = _batch_var.set(coord)
    try:
        assert claim_scope_activation("open_aina") is True
        assert claim_scope_activation("open_aina") is False
    finally:
        _batch_var.reset(token)
