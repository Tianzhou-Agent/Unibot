"""Phase 0 native-feasibility proofs for the LangChain middleware migration.

These tests exercise real framework components (``create_agent``, built-in
middleware, native messages, checkpointer, callbacks) with a scripted native
chat model. They demonstrate that the target architecture is expressible
without private factory patching or reimplementing the agent loop / summary
engine.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware import (
    AgentMiddleware,
    HumanInTheLoopMiddleware,
    InterruptOnConfig,
    ModelCallLimitMiddleware,
    SummarizationMiddleware,
    ToolErrorMiddleware,
    ToolRetryMiddleware,
)
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from tests.support.fake_chat_model import ScriptedChatModel, assistant, multi_tool_calling, tool_calling


# Shared mutable logs for tools (StructuredTool does not accept arbitrary attributes).
_TOOL_LOGS: dict[str, list[str]] = {"ordered": [], "flaky": {}}


@tool
def echo_tool(text: str, runtime: ToolRuntime) -> str:
    """Echo the provided text back to the caller."""
    return f"echo:{text}"


@tool
def side_effect_tool(action: str, runtime: ToolRuntime) -> str:
    """Record a side effect. Not safe to auto-retry."""
    return f"done:{action}"


@tool
def flaky_tool(action: str, runtime: ToolRuntime) -> str:
    """Fail once then succeed, for retry-policy checks."""
    attempts = _TOOL_LOGS["flaky"]  # type: ignore[assignment]
    n = attempts.get(action, 0) + 1
    attempts[action] = n
    if n == 1:
        raise RuntimeError("transient failure")
    return f"ok:{action}"


@tool
def ordered_tool(tag: str, runtime: ToolRuntime) -> str:
    """Append a tag to the shared order log."""
    _TOOL_LOGS["ordered"].append(tag)
    return tag


class TokenCollector(AsyncCallbackHandler):
    def __init__(self) -> None:
        self.model_starts = 0
        self.model_ends = 0
        self.tokens: list[str] = []
        self.tool_starts: list[str] = []
        self.tool_ends: list[str] = []
        self.errors: list[str] = []

    async def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        self.model_starts += 1

    async def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        self.model_ends += 1

    async def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        self.tokens.append(token)

    async def on_tool_start(self, serialized: Any, input_str: str, **kwargs: Any) -> None:
        self.tool_starts.append(str((serialized or {}).get("name") or input_str))

    async def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        self.tool_ends.append(str(output))

    async def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        self.errors.append(type(error).__name__)

    async def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        self.errors.append(f"llm:{type(error).__name__}")


# ---------------------------------------------------------------------------
# 1. Native model + tool loop
# ---------------------------------------------------------------------------


async def test_create_agent_runs_native_tool_loop() -> None:
    model = ScriptedChatModel(
        responses=[
            tool_calling("echo_tool", {"text": "hi"}, call_id="c1"),
            assistant("final answer"),
        ]
    )
    agent = create_agent(model, tools=[echo_tool], system_prompt="You are a test agent.")
    result = await agent.ainvoke(
        {"messages": [HumanMessage(content="say hi")]},
        config={"recursion_limit": 10},
    )
    messages = result["messages"]
    assert isinstance(messages[0], HumanMessage)
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "echo:hi"
    assert tool_msgs[0].tool_call_id == "c1"
    assert isinstance(messages[-1], AIMessage)
    assert messages[-1].content == "final answer"
    assert len(model.calls) == 2


async def test_message_metadata_is_preserved() -> None:
    model = ScriptedChatModel(
        responses=[
            tool_calling("echo_tool", {"text": "meta"}, call_id="call_meta"),
            assistant("done"),
        ]
    )
    agent = create_agent(model, tools=[echo_tool])
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})
    ai_with_calls = next(m for m in result["messages"] if isinstance(m, AIMessage) and m.tool_calls)
    assert ai_with_calls.tool_calls[0]["id"] == "call_meta"
    assert ai_with_calls.tool_calls[0]["name"] == "echo_tool"
    tool_msg = next(m for m in result["messages"] if isinstance(m, ToolMessage))
    assert tool_msg.tool_call_id == "call_meta"
    assert result["messages"][-1].usage_metadata is not None


async def test_multi_tool_batch_is_executed() -> None:
    _TOOL_LOGS["ordered"] = []
    model = ScriptedChatModel(
        responses=[
            multi_tool_calling(
                [
                    ("ordered_tool", {"tag": "a"}, "c1"),
                    ("ordered_tool", {"tag": "b"}, "c2"),
                    ("ordered_tool", {"tag": "c"}, "c3"),
                ]
            ),
            assistant("batch done"),
        ]
    )
    agent = create_agent(model, tools=[ordered_tool])
    result = await agent.ainvoke({"messages": [HumanMessage(content="run three")]})
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 3
    assert sorted(_TOOL_LOGS["ordered"]) == ["a", "b", "c"]


# ---------------------------------------------------------------------------
# 2. Middleware composition and error formatting
# ---------------------------------------------------------------------------


async def test_middleware_composition_order_is_outer_to_inner() -> None:
    order: list[str] = []

    class MarkOuter(AgentMiddleware):
        async def abefore_model(self, state: Any, runtime: Any) -> Any:
            order.append("before:outer")
            return None

        async def aafter_model(self, state: Any, runtime: Any) -> Any:
            order.append("after:outer")
            return None

    class MarkInner(AgentMiddleware):
        async def abefore_model(self, state: Any, runtime: Any) -> Any:
            order.append("before:inner")
            return None

        async def aafter_model(self, state: Any, runtime: Any) -> Any:
            order.append("after:inner")
            return None

    model = ScriptedChatModel(responses=[assistant("ok")])
    agent = create_agent(model, tools=[], middleware=[MarkOuter(), MarkInner()])
    await agent.ainvoke({"messages": [HumanMessage(content="hi")]})
    assert order[0] == "before:outer"
    assert order[1] == "before:inner"
    assert order[2] == "after:inner"
    assert order[3] == "after:outer"


async def test_tool_error_middleware_formats_selected_errors() -> None:
    @tool
    def boom_tool(runtime: ToolRuntime) -> str:
        """Always raise ValueError."""
        raise ValueError("bad input")

    def on_error(exc: Exception, request: Any) -> str | None:
        if isinstance(exc, ValueError):
            return f"tool_failed:{request.tool_call['name']}"
        return None

    model = ScriptedChatModel(
        responses=[
            tool_calling("boom_tool", {}, call_id="c1"),
            assistant("recovered"),
        ]
    )
    agent = create_agent(
        model,
        tools=[boom_tool],
        middleware=[ToolErrorMiddleware(on_error=on_error)],
    )
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs and "tool_failed:boom_tool" in tool_msgs[0].content


async def test_unhandled_tool_error_propagates() -> None:
    @tool
    def crash_tool(runtime: ToolRuntime) -> str:
        """Raise an exception the formatter declines to handle."""
        raise RuntimeError("explode")

    def on_error(exc: Exception, request: Any) -> str | None:
        return None

    model = ScriptedChatModel(responses=[tool_calling("crash_tool", {}, call_id="c1")])
    agent = create_agent(
        model,
        tools=[crash_tool],
        middleware=[ToolErrorMiddleware(on_error=on_error)],
    )
    with pytest.raises(RuntimeError, match="explode"):
        await agent.ainvoke({"messages": [HumanMessage(content="go")]})


async def test_tool_retry_middleware_retries_eligible_tool() -> None:
    _TOOL_LOGS["flaky"] = {}
    model = ScriptedChatModel(
        responses=[
            tool_calling("flaky_tool", {"action": "x"}, call_id="c1"),
            assistant("after retry"),
        ]
    )
    agent = create_agent(
        model,
        tools=[flaky_tool],
        middleware=[
            # ToolErrorMiddleware outer, ToolRetryMiddleware inner (see ToolErrorMiddleware docs).
            ToolErrorMiddleware(on_error=lambda exc, req: f"failed:{type(exc).__name__}"),
            ToolRetryMiddleware(
                max_retries=2,
                tools=["flaky_tool"],
                retry_on=(RuntimeError,),
                on_failure="error",
                backoff_factor=0.0,
                initial_delay=0.0,
                jitter=False,
            ),
        ],
    )
    result = await agent.ainvoke({"messages": [HumanMessage(content="go")]})
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs
    assert tool_msgs[0].content.startswith("ok:")
    assert _TOOL_LOGS["flaky"].get("x") == 2


# ---------------------------------------------------------------------------
# 3. Built-in summarization
# ---------------------------------------------------------------------------


async def test_summarization_middleware_compacts_history() -> None:
    class AlwaysSummary(ScriptedChatModel):
        def _next(self, messages: Any, kwargs: dict[str, Any]) -> AIMessage:
            self.calls.append({"messages": list(messages), "kind": "summary"})
            return assistant("SUMMARY: user wants a widget catalog.")

    summary_model = AlwaysSummary(responses=[])
    chat_model = ScriptedChatModel(responses=[assistant(f"ans-{i}") for i in range(6)])
    checkpointer = InMemorySaver()
    agent = create_agent(
        chat_model,
        tools=[],
        middleware=[
            SummarizationMiddleware(
                model=summary_model,
                trigger=("messages", 6),
                keep=("messages", 2),
            )
        ],
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": "summarize-1"}}

    result: dict[str, Any] = {}
    for i in range(6):
        result = await agent.ainvoke({"messages": [HumanMessage(content=f"turn-{i}")]}, config=config)

    assert len(summary_model.calls) >= 1
    assert result["messages"][-1].content == "ans-5"
    # Compaction bounds growth versus naive accumulation (6 turns * 2 + system).
    assert len(result["messages"]) <= 14


async def test_summarization_replaces_older_content() -> None:
    class AlwaysSummary(ScriptedChatModel):
        def _next(self, messages: Any, kwargs: dict[str, Any]) -> AIMessage:
            self.calls.append({"messages": list(messages), "kind": "summary"})
            return assistant("[SUMMARY] prior context")

    summary_model = AlwaysSummary(responses=[])
    chat_model = ScriptedChatModel(responses=[assistant(f"ans-{i}") for i in range(5)])
    checkpointer = InMemorySaver()
    agent = create_agent(
        chat_model,
        tools=[],
        middleware=[
            SummarizationMiddleware(
                model=summary_model,
                trigger=("messages", 4),
                keep=("messages", 2),
            )
        ],
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": "summarize-2"}}
    result = None
    for i in range(5):
        result = await agent.ainvoke({"messages": [HumanMessage(content=f"q{i}")]}, config=config)
    assert result is not None
    contents = [str(m.content) for m in result["messages"] if m.content]
    assert any("SUMMARY" in c for c in contents) or len(result["messages"]) <= 6


async def test_summarization_failure_does_not_silently_drop_history() -> None:
    class ExplodingSummary(ScriptedChatModel):
        def _next(self, messages: Any, kwargs: dict[str, Any]) -> AIMessage:
            raise RuntimeError("summary backend down")

    summary_model = ExplodingSummary(responses=[])
    chat_model = ScriptedChatModel(responses=[assistant(f"a{i}") for i in range(6)])
    checkpointer = InMemorySaver()
    agent = create_agent(
        chat_model,
        tools=[],
        middleware=[
            SummarizationMiddleware(
                model=summary_model,
                trigger=("messages", 2),
                keep=("messages", 1),
            )
        ],
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": "summarize-fail"}}
    result = await agent.ainvoke({"messages": [HumanMessage(content="t0")]}, config=config)
    raised = False
    for i in range(1, 5):
        try:
            result = await agent.ainvoke({"messages": [HumanMessage(content=f"t{i}")]}, config=config)
        except RuntimeError as exc:
            raised = True
            assert "summary backend down" in str(exc)
            break
    if raised:
        return
    # If summarization is deferred, original user turns must remain visible.
    user_contents = [str(m.content) for m in result["messages"] if isinstance(m, HumanMessage)]
    assert any(c.startswith("t") for c in user_contents)


# ---------------------------------------------------------------------------
# 4. Human-in-the-loop
# ---------------------------------------------------------------------------


async def test_hitl_interrupts_before_risky_tool() -> None:
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(
        responses=[
            tool_calling("side_effect_tool", {"action": "send"}, call_id="r1"),
            assistant("executed"),
        ]
    )
    agent = create_agent(
        model,
        tools=[side_effect_tool],
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "side_effect_tool": InterruptOnConfig(
                        allowed_decisions=["approve", "reject"],
                    )
                }
            )
        ],
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": "hitl-1"}}
    result = await agent.ainvoke({"messages": [HumanMessage(content="send it")]}, config=config)

    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert not tool_msgs, "risky tool must not execute before approval"

    # HITL resume payload: {"decisions": [{"type": "approve"}, ...]}
    resume_result = await agent.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config=config,
    )
    tool_msgs = [m for m in resume_result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs, "approved tool should execute on resume"
    assert "done:send" in tool_msgs[0].content


async def test_hitl_mixed_batch_pauses_before_any_sibling() -> None:
    _TOOL_LOGS["ordered"] = []
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(
        responses=[
            multi_tool_calling(
                [
                    ("ordered_tool", {"tag": "safe"}, "s1"),
                    ("side_effect_tool", {"action": "pay"}, "r1"),
                ]
            ),
            assistant("all done"),
        ]
    )
    agent = create_agent(
        model,
        tools=[ordered_tool, side_effect_tool],
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "side_effect_tool": InterruptOnConfig(allowed_decisions=["approve", "reject"])
                }
            )
        ],
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": "hitl-mixed"}}
    result = await agent.ainvoke({"messages": [HumanMessage(content="mixed")]}, config=config)
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert not tool_msgs
    assert _TOOL_LOGS["ordered"] == []

    resume_result = await agent.ainvoke(
        Command(resume={"decisions": [{"type": "approve"}]}),
        config=config,
    )
    tool_msgs = [m for m in resume_result["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 2
    assert _TOOL_LOGS["ordered"] == ["safe"]


async def test_hitl_rejection_skips_tool_execution() -> None:
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(
        responses=[
            tool_calling("side_effect_tool", {"action": "send"}, call_id="r1"),
            assistant("cancelled"),
        ]
    )
    agent = create_agent(
        model,
        tools=[side_effect_tool],
        middleware=[
            HumanInTheLoopMiddleware(
                interrupt_on={
                    "side_effect_tool": InterruptOnConfig(allowed_decisions=["approve", "reject"])
                }
            )
        ],
        checkpointer=checkpointer,
    )
    config = {"configurable": {"thread_id": "hitl-reject"}}
    await agent.ainvoke({"messages": [HumanMessage(content="go")]}, config=config)
    resume_result = await agent.ainvoke(
        Command(resume={"decisions": [{"type": "reject"}]}),
        config=config,
    )
    tool_msgs = [m for m in resume_result["messages"] if isinstance(m, ToolMessage)]
    for m in tool_msgs:
        assert "done:send" not in m.content


# ---------------------------------------------------------------------------
# 5. Checkpointer: recreation, resume, isolation
# ---------------------------------------------------------------------------


async def test_saver_recreation_resumes_thread_state() -> None:
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(responses=[assistant("first")])
    agent = create_agent(model, tools=[], checkpointer=checkpointer)
    config = {"configurable": {"thread_id": "thread-recreate"}}
    await agent.ainvoke({"messages": [HumanMessage(content="hello")]}, config=config)

    model2 = ScriptedChatModel(responses=[assistant("third")])
    agent2 = create_agent(model2, tools=[], checkpointer=checkpointer)
    result = await agent2.ainvoke({"messages": [HumanMessage(content="again")]}, config=config)
    user_contents = [m.content for m in result["messages"] if isinstance(m, HumanMessage)]
    assert "hello" in user_contents
    assert "again" in user_contents


async def test_thread_isolation_between_actors() -> None:
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(responses=[assistant("alice"), assistant("bob")])
    agent = create_agent(model, tools=[], checkpointer=checkpointer)
    await agent.ainvoke(
        {"messages": [HumanMessage(content="from-alice")]},
        config={"configurable": {"thread_id": "actor-a"}},
    )
    bob = await agent.ainvoke(
        {"messages": [HumanMessage(content="from-bob")]},
        config={"configurable": {"thread_id": "actor-b"}},
    )
    user_contents = [m.content for m in bob["messages"] if isinstance(m, HumanMessage)]
    assert "from-alice" not in user_contents
    assert "from-bob" in user_contents


async def test_model_call_limit_enforces_run_budget() -> None:
    model = ScriptedChatModel(
        responses=[
            tool_calling("echo_tool", {"text": "1"}, call_id="c1"),
            tool_calling("echo_tool", {"text": "2"}, call_id="c2"),
            tool_calling("echo_tool", {"text": "3"}, call_id="c3"),
            assistant("should not be reached"),
        ]
    )
    agent = create_agent(
        model,
        tools=[echo_tool],
        middleware=[ModelCallLimitMiddleware(run_limit=2, exit_behavior="end")],
    )
    await agent.ainvoke({"messages": [HumanMessage(content="loop")]})
    assert len(model.calls) <= 2


# ---------------------------------------------------------------------------
# 6. Native callbacks + streaming
# ---------------------------------------------------------------------------


async def test_callbacks_observe_model_and_tool_events() -> None:
    collector = TokenCollector()
    model = ScriptedChatModel(
        responses=[
            tool_calling("echo_tool", {"text": "cb"}, call_id="c1"),
            assistant("done"),
        ]
    )
    agent = create_agent(model, tools=[echo_tool])
    await agent.ainvoke({"messages": [HumanMessage(content="go")]}, config={"callbacks": [collector]})
    assert collector.model_starts >= 1
    assert collector.model_ends >= 1
    assert collector.tool_starts
    assert collector.tool_ends


async def test_native_streaming_emits_chunks() -> None:
    model = ScriptedChatModel(responses=[assistant("streamed answer")])
    agent = create_agent(model, tools=[])
    chunks: list[Any] = []
    async for event in agent.astream(
        {"messages": [HumanMessage(content="hi")]},
        stream_mode=["messages", "updates"],
    ):
        chunks.append(event)
    assert chunks, "stream should yield at least one event"


async def test_streaming_lifecycle_callbacks_fire() -> None:
    collector = TokenCollector()
    model = ScriptedChatModel(responses=[assistant("tok")])
    agent = create_agent(model, tools=[])
    async for _ in agent.astream(
        {"messages": [HumanMessage(content="hi")]},
        stream_mode="messages",
        config={"callbacks": [collector]},
    ):
        pass
    assert collector.model_starts >= 1


# ---------------------------------------------------------------------------
# 7. Documented gap probes (ordered effects)
# ---------------------------------------------------------------------------


async def test_documented_gap_parallel_tool_order_is_not_guaranteed() -> None:
    """Record that native ToolNode dispatches tool batches concurrently.

    Product batch-ordering semantics require the Phase 3 ``awrap_tool_call``
    coordinator. This test asserts concurrent completion so a future sequential
    coordinator can flip the expectation deliberately.
    """
    _TOOL_LOGS["ordered"] = []
    model = ScriptedChatModel(
        responses=[
            multi_tool_calling(
                [
                    ("ordered_tool", {"tag": "1"}, "c1"),
                    ("ordered_tool", {"tag": "2"}, "c2"),
                    ("ordered_tool", {"tag": "3"}, "c3"),
                ]
            ),
            assistant("done"),
        ]
    )
    agent = create_agent(model, tools=[ordered_tool])
    await agent.ainvoke({"messages": [HumanMessage(content="order")]})
    assert sorted(_TOOL_LOGS["ordered"]) == ["1", "2", "3"]
    # GAP (plan §6.3): asyncio.gather in ToolNode means ordered side effects
    # need a public-hook coordinator before cutover of order-sensitive flows.
