"""Runtime tool and model policies: ordered batches, dedup, retries, limits, output guards, malformed calls."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime

from langchain.agents.middleware import ToolErrorMiddleware

from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.core.agent_runtime import build_agent
from tianzhou_agent_platform.core.agent_runtime.middleware.model_policy import (
    InvalidToolCallMiddleware,
    ModelCallBudgetMiddleware,
    OutputGuardMiddleware,
    RequestBudgetGuard,
    reject_truncated_call,
)
from tianzhou_agent_platform.core.agent_runtime.middleware.tool_policy import (
    BatchDeduper,
    OrderedBatchMiddleware,
    tool_signature,
)
from tianzhou_agent_platform.services.agent_integration.capabilities import Capability
from tianzhou_agent_platform.services.agent_integration.capability_tools import format_error_envelope
from tianzhou_agent_platform.services.agent_integration.retries import (
    is_transient_error,
    tool_retry_middleware,
)
from tianzhou_agent_platform.services.agent_integration.scope import filter_advertised_tools
from tests.support.fake_chat_model import ScriptedChatModel, assistant, multi_tool_calling, tool_calling

_LOG: list[str] = []


async def _format(exc: Exception, request: object) -> str:
    return format_error_envelope(exc)


def build_chat_agent(*, model, tools, middleware=()):  # type: ignore[no-untyped-def]
    """The native agent with the runtime's batch and output policies (as the runner composes them)."""
    return build_agent(
        model=model,
        tools=tools,
        middleware=[
            OrderedBatchMiddleware(),
            OutputGuardMiddleware(),
            ToolErrorMiddleware(aon_error=_format),
            *middleware,
        ],
    )


def _remote_tool(name: str, *, side_effect_level: str, retries: int) -> Capability:
    record = ToolRecord(
        tool_id=name,
        name=name,
        description="Remote test tool.",
        input_schema={"type": "object"},
        endpoint="https://tool.invalid/invoke",
        side_effect_level=side_effect_level,
        retries=retries,
    )
    return Capability(
        kind="tool",
        capability_id=name,
        function_name=name,
        display_name=name,
        description="",
        input_schema={"type": "object"},
        requires_confirmation=side_effect_level == "high",
        value=record,
    )


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
    agent = build_chat_agent(model=model, tools=[ordered_tool])
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
        tools=[boom, after_tool]
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
    agent = build_chat_agent(model=model, tools=[ordered_tool])
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
    registry = {
        "flaky_readonly": _remote_tool("flaky_readonly", side_effect_level="none", retries=1),
        "side_effect_tool": _remote_tool("side_effect_tool", side_effect_level="low", retries=3),
    }
    agent = build_chat_agent(
        model=model,
        tools=[flaky_readonly, side_effect_tool],
        middleware=tool_retry_middleware(registry),
    )
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs[0].content.startswith("ok:")
    assert flaky_readonly._attempts.get("q") == 2


def test_side_effect_tool_is_not_in_retry_eligible_set() -> None:
    registry = {
        "flaky_readonly": _remote_tool("flaky_readonly", side_effect_level="none", retries=2),
        "side_effect_tool": _remote_tool("side_effect_tool", side_effect_level="low", retries=2),
        "no_budget": _remote_tool("no_budget", side_effect_level="none", retries=0),
    }
    [mw] = tool_retry_middleware(registry)
    # Side-effecting tools and tools without a retry budget must not appear in the native retry list.
    assert [name for name in registry if mw._should_retry_tool(name)] == ["flaky_readonly"]
    assert mw.max_retries == 2


# ---------------------------------------------------------------------------
# Limits and output guards
# ---------------------------------------------------------------------------


async def test_model_call_budget_ends_the_run_with_the_reply() -> None:
    model = ScriptedChatModel(
        responses=[tool_calling("ordered_tool", {"tag": str(n)}, call_id=f"c{n}") for n in range(3)]
    )
    budget = ModelCallBudgetMiddleware(run_limit=2, reply="stopped")
    result = await build_chat_agent(model=model, tools=[ordered_tool], middleware=[budget]).ainvoke(
        {"messages": [HumanMessage(content="loop")]}
    )

    assert len(model.calls) == 2
    assert budget.exceeded == "stopped"
    assert result["messages"][-1].content == "stopped"
    # Every call of the run was answered before it stopped.
    assert len([m for m in result["messages"] if isinstance(m, ToolMessage)]) == 2


async def test_request_budget_guard_refuses_oversized_requests_locally() -> None:
    model = ScriptedChatModel(responses=[assistant("never sent")])
    guard = RequestBudgetGuard(
        input_budget_tokens=50,
        count_tokens=lambda messages, tools: sum(len(str(message["content"])) for message in messages),
    )
    agent = build_agent(model=model, middleware=[guard])

    result = await agent.ainvoke({"messages": [HumanMessage(content="x" * 100)]})

    assert "exceeds the model context budget" in result["messages"][-1].content
    assert guard.exceeded == result["messages"][-1].content
    assert model.calls == []


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
    agent = build_chat_agent(model=model, tools=[must_not_run])
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


# ---------------------------------------------------------------------------
# Output guard and malformed tool calls
# ---------------------------------------------------------------------------


async def test_output_guard_reports_failures_and_closes_truncated_invalid_calls() -> None:
    truncated = AIMessage(
        content="partial",
        tool_calls=[{"name": "ordered_tool", "args": {"tag": "x"}, "id": "c1"}],
        invalid_tool_calls=[{"name": "ordered_tool", "args": '{"tag": ', "id": "c2", "error": "bad json"}],
        response_metadata={"finish_reason": "length"},
    )
    guard = OutputGuardMiddleware()
    result = await build_agent(
        model=ScriptedChatModel(responses=[truncated]), tools=[ordered_tool], middleware=[guard]
    ).ainvoke({"messages": [HumanMessage(content="go")]})

    closed = {m.tool_call_id for m in result["messages"] if isinstance(m, ToolMessage)}
    assert closed == {"c1", "c2"}
    assert guard.failure == f"partial\n\n{OutputGuardMiddleware.TRUNCATION_NOTICE}"
    assert result["messages"][-1].content == guard.failure

    empty_guard = OutputGuardMiddleware()
    await build_agent(model=ScriptedChatModel(responses=[assistant("")]), middleware=[empty_guard]).ainvoke(
        {"messages": [HumanMessage(content="go")]}
    )
    assert empty_guard.failure == OutputGuardMiddleware.EMPTY_REPLY


async def test_malformed_tool_calls_are_answered_and_the_model_asked_again() -> None:
    _LOG.clear()
    answered: list[str] = []

    async def respond(call: dict) -> str:
        answered.append(call["id"])
        return f'{{"error": {{"code": "INVALID_REQUEST", "message": "{call["error"]}"}}}}'

    model = ScriptedChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "ordered_tool", "args": {"tag": "ok"}, "id": "c1"}],
                invalid_tool_calls=[{"name": "ordered_tool", "args": "{oops", "id": "c2", "error": "bad json"}],
            ),
            AIMessage(
                content="",
                invalid_tool_calls=[{"name": "ordered_tool", "args": "{oops", "id": "c3", "error": "bad json"}],
            ),
            assistant("fixed"),
        ]
    )
    guard = OutputGuardMiddleware()
    result = await build_agent(
        model=model,
        tools=[ordered_tool],
        middleware=[InvalidToolCallMiddleware(respond), OrderedBatchMiddleware(), guard],
    ).ainvoke({"messages": [HumanMessage(content="go")]})

    assert _LOG == ["ok"]
    assert answered == ["c2", "c3"]
    assert result["messages"][-1].content == "fixed"
    assert guard.failure is None
    # The model saw every answer: no call is left without a result.
    results = {m.tool_call_id for m in result["messages"] if isinstance(m, ToolMessage)}
    assert results == {"c1", "c2", "c3"}


async def test_malformed_tool_calls_cannot_loop_past_the_model_call_limit() -> None:
    async def respond(call: dict) -> str:
        return '{"error": {"code": "INVALID_REQUEST"}}'

    def malformed(**_: object) -> AIMessage:
        return AIMessage(content="", invalid_tool_calls=[{"name": "x", "args": "{", "id": "c", "error": "bad"}])

    model = ScriptedChatModel(responses=[malformed] * 5)
    budget = ModelCallBudgetMiddleware(run_limit=3, reply="stopped")
    agent = build_agent(model=model, middleware=[InvalidToolCallMiddleware(respond), budget])
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})
    assert len(model.calls) == 3
    assert result["messages"][-1].content == "stopped"


# ---------------------------------------------------------------------------
# Run isolation
# ---------------------------------------------------------------------------


async def test_concurrent_runs_never_share_batches_or_attempt_budgets() -> None:
    import asyncio

    _LOG.clear()
    gate = asyncio.Event()

    @tool
    async def slow_tool(tag: str) -> str:
        """Wait until both runs are inside their batch."""
        _LOG.append(tag)
        await gate.wait()
        return tag

    def batch(tag: str) -> AIMessage:
        # Both runs use the same call ids and arguments.
        return AIMessage(
            content="",
            tool_calls=[
                {"name": "slow_tool", "args": {"tag": "same"}, "id": "c1"},
                {"name": "ordered_tool", "args": {"tag": tag}, "id": "c2"},
            ],
        )

    agents = [
        build_chat_agent(model=ScriptedChatModel(responses=[batch(tag), assistant(tag)]), tools=[slow_tool, ordered_tool])
        for tag in ("a", "b")
    ]
    runs = [asyncio.create_task(agent.ainvoke({"messages": [HumanMessage(content="go")]})) for agent in agents]
    while _LOG.count("same") < 2:
        await asyncio.sleep(0.01)
    gate.set()
    results = await asyncio.gather(*runs)

    # Each run executed its own identical call once and released its own successor.
    assert sorted(_LOG) == ["a", "b", "same", "same"]
    for result in results:
        assert all("error" not in m.content for m in result["messages"] if isinstance(m, ToolMessage))
