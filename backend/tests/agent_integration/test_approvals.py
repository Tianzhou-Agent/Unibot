"""Native HITL for risky capabilities: batch pause, approval mapping, resume and terminal denial."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from tianzhou_agent_platform.core.agent_runtime import build_agent
from tianzhou_agent_platform.core.agent_runtime.middleware.approval_policy import TerminalDenialMiddleware
from tianzhou_agent_platform.services.agent_integration.approvals import (
    CANCELLED_REPLY,
    DENIED_TOOL_RESULT,
    approval_record,
    build_approval_middleware,
    resume_decisions,
)
from tianzhou_agent_platform.services.agent_integration.capabilities import Capability
from tests.support.fake_chat_model import ScriptedChatModel, assistant, multi_tool_calling, tool_calling

EXECUTED: list[str] = []


@tool
def risky_tool(action: str) -> str:
    """Requires approval."""
    EXECUTED.append(f"risky:{action}")
    return f"done:{action}"


@tool
def safe_tool(tag: str) -> str:
    """No approval required."""
    EXECUTED.append(f"safe:{tag}")
    return f"safe:{tag}"


def _capability(name: str, *, requires_confirmation: bool) -> Capability:
    return Capability(
        kind="builtin",
        capability_id=name,
        function_name=name,
        display_name=name.replace("_", " ").title(),
        description="",
        input_schema={"type": "object", "properties": {"action": {"type": "string"}}, "required": ["action"]},
        requires_confirmation=requires_confirmation,
        value=name,
    )


REGISTRY = {
    "risky_tool": _capability("risky_tool", requires_confirmation=True),
    "safe_tool": _capability("safe_tool", requires_confirmation=False),
}


def _agent(model: ScriptedChatModel, checkpointer: InMemorySaver, *, denied: bool = False):  # type: ignore[no-untyped-def]
    return build_agent(
        model=model,
        tools=[safe_tool, risky_tool],
        middleware=[
            TerminalDenialMiddleware(denied=denied, tool_result=DENIED_TOOL_RESULT, reply=CANCELLED_REPLY),
            build_approval_middleware(REGISTRY),
        ],
        checkpointer=checkpointer,
    )


def test_only_capabilities_requiring_confirmation_interrupt() -> None:
    middleware = build_approval_middleware(REGISTRY)
    assert set(middleware.interrupt_on) == {"risky_tool"}
    assert middleware.interrupt_on["risky_tool"]["allowed_decisions"] == ["approve", "reject"]
    assert middleware.interrupt_on["risky_tool"]["description"] == "Risky Tool"


async def test_mixed_risky_batch_pauses_before_any_sibling_and_maps_to_an_approval() -> None:
    EXECUTED.clear()
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(
        responses=[
            multi_tool_calling([("safe_tool", {"tag": "s"}, "s1"), ("risky_tool", {"action": "pay"}, "r1")]),
            assistant("all done"),
        ]
    )
    agent = _agent(model, checkpointer)
    config = {"configurable": {"thread_id": "lc-v2:c1"}}
    await agent.ainvoke({"messages": [HumanMessage(content="mixed")]}, config=config)
    state = await agent.aget_state(config)

    assert EXECUTED == []
    [interrupt] = state.interrupts
    record = approval_record(
        interrupt=interrupt,
        messages=state.values["messages"],
        conversation_id="c1",
        user_id="u",
        tenant_id="t",
        trace_id="trace_1",
        thread_id="lc-v2:c1",
    )
    assert record.status == "pending"
    assert record.capability_names == ["Risky Tool"]
    assert [call["function"]["name"] for call in record.tool_calls] == ["safe_tool", "risky_tool"]
    assert record.runtime_ref == {
        "engine": "langgraph",
        "thread_id": "lc-v2:c1",
        "interrupt_id": interrupt.id,
        "action_count": 1,
    }

    # A recreated agent over the same saver resumes the original checkpointed calls.
    resumed = _agent(ScriptedChatModel(responses=[assistant("all done")]), checkpointer)
    result = await resumed.ainvoke(Command(resume=resume_decisions(interrupt, approve=True)), config=config)
    assert sorted(EXECUTED) == ["risky:pay", "safe:s"]
    assert result["messages"][-1].content == "all done"


async def test_invalid_risky_arguments_do_not_request_approval() -> None:
    EXECUTED.clear()
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(responses=[tool_calling("risky_tool", {"wrong": 1}, call_id="r1"), assistant("fix the input")])
    agent = _agent(model, checkpointer)
    config = {"configurable": {"thread_id": "lc-v2:c2"}}
    await agent.ainvoke({"messages": [HumanMessage(content="go")]}, config=config)

    assert not (await agent.aget_state(config)).interrupts


async def test_denial_closes_the_batch_without_another_model_call() -> None:
    EXECUTED.clear()
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(
        responses=[multi_tool_calling([("safe_tool", {"tag": "s"}, "s1"), ("risky_tool", {"action": "pay"}, "r1")])]
    )
    config = {"configurable": {"thread_id": "lc-v2:c3"}}
    await _agent(model, checkpointer).ainvoke({"messages": [HumanMessage(content="go")]}, config=config)
    [interrupt] = (await _agent(model, checkpointer).aget_state(config)).interrupts

    # The scripted model has no response left: any further model call would fail the run.
    result = await _agent(model, checkpointer, denied=True).ainvoke(
        Command(resume=resume_decisions(interrupt, approve=False)),
        config=config,
    )

    assert EXECUTED == []
    tool_messages = {message.tool_call_id: message for message in result["messages"] if isinstance(message, ToolMessage)}
    assert tool_messages["s1"].content == DENIED_TOOL_RESULT
    assert "rejected" in tool_messages["r1"].content
    assert isinstance(result["messages"][-1], AIMessage)
    assert result["messages"][-1].content == CANCELLED_REPLY


def test_resume_decisions_cover_every_paused_action() -> None:
    from langgraph.types import Interrupt

    interrupt = Interrupt(value={"action_requests": [{"name": "a"}, {"name": "b"}]}, id="i1")
    assert resume_decisions(interrupt, approve=True) == {"decisions": [{"type": "approve"}, {"type": "approve"}]}
    assert resume_decisions(interrupt, approve=False) == {"decisions": [{"type": "reject"}, {"type": "reject"}]}
