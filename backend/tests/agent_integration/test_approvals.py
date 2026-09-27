"""Phase 4: native HITL mapping, resume payloads and supersession."""

from __future__ import annotations

import pytest
from langchain_core.messages import HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command

from tianzhou_agent_platform.services.agent_integration.approvals import (
    approval_record_from_interrupt,
    approve_all,
    build_hitl_middleware,
    is_interrupt_payload,
    native_resume_payload,
    reject_all,
)
from tianzhou_agent_platform.services.agent_integration.builder import build_chat_agent
from tests.support.fake_chat_model import ScriptedChatModel, assistant, multi_tool_calling, tool_calling


@tool
def risky_tool(action: str, runtime: ToolRuntime) -> str:
    """Requires approval."""
    return f"done:{action}"


@tool
def safe_tool(tag: str, runtime: ToolRuntime) -> str:
    """No approval required."""
    return f"safe:{tag}"


def test_build_hitl_middleware_maps_decisions() -> None:
    mw = build_hitl_middleware(
        risky_tools={
            "risky_tool": ["approve", "reject"],
            "auto_tool": False,
            "full_tool": True,
        }
    )
    assert "risky_tool" in mw.interrupt_on
    assert "auto_tool" not in mw.interrupt_on
    assert "full_tool" in mw.interrupt_on
    assert set(mw.interrupt_on["risky_tool"]["allowed_decisions"]) == {"approve", "reject"}


def test_resume_payloads() -> None:
    assert approve_all(2) == {"decisions": [{"type": "approve"}, {"type": "approve"}]}
    assert reject_all(1) == {"decisions": [{"type": "reject"}]}
    assert is_interrupt_payload(approve_all(1))
    assert not is_interrupt_payload({"foo": 1})


def test_approval_record_binds_runtime_ref() -> None:
    record = approval_record_from_interrupt(
        conversation_id="c1",
        user_id="u",
        tenant_id="t",
        trace_id="tr",
        tool_calls=[{"id": "x", "name": "risky_tool", "args": {}}],
        capability_names=["Risky"],
        runtime_ref={"thread_id": "lc-v2:c1", "interrupt_id": "i1"},
        run_generation=3,
    )
    assert record.status == "pending"
    assert record.runtime_ref is not None
    assert record.runtime_ref["thread_id"] == "lc-v2:c1"
    assert record.run_generation == 3


async def test_mixed_risky_batch_pauses_before_any_sibling() -> None:
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(
        responses=[
            multi_tool_calling(
                [
                    ("safe_tool", {"tag": "s"}, "s1"),
                    ("risky_tool", {"action": "pay"}, "r1"),
                ]
            ),
            assistant("all done"),
        ]
    )
    agent = build_chat_agent(
        model=model,
        tools=[safe_tool, risky_tool],
        middleware=[
            build_hitl_middleware(risky_tools={"risky_tool": ["approve", "reject"]})
        ],
        checkpointer=checkpointer,
        use_model_call_limit=False,
    )
    config = {"configurable": {"thread_id": "hitl-mixed-p4"}}
    result = await agent.ainvoke({"messages": [HumanMessage(content="mixed")]}, config=config)
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert not tool_msgs, "no sibling may execute before approval"

    resume = await agent.ainvoke(Command(resume=approve_all(1)), config=config)
    tool_msgs = [m for m in resume["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 2
    assert any("safe:" in m.content for m in tool_msgs)
    assert any("done:pay" in m.content for m in tool_msgs)


async def test_deny_closes_batch_without_side_effect() -> None:
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(
        responses=[
            tool_calling("risky_tool", {"action": "send"}, call_id="r1"),
            assistant("should not matter"),
        ]
    )
    agent = build_chat_agent(
        model=model,
        tools=[risky_tool],
        middleware=[build_hitl_middleware(risky_tools={"risky_tool": ["approve", "reject"]})],
        checkpointer=checkpointer,
        use_model_call_limit=False,
    )
    config = {"configurable": {"thread_id": "hitl-deny-p4"}}
    await agent.ainvoke({"messages": [HumanMessage(content="go")]}, config=config)
    resume = await agent.ainvoke(Command(resume=reject_all(1)), config=config)
    for m in resume["messages"]:
        if isinstance(m, ToolMessage):
            assert "done:send" not in m.content


async def test_saver_recreation_resumes_original_calls() -> None:
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(
        responses=[
            tool_calling("risky_tool", {"action": "x"}, call_id="r1"),
            assistant("executed"),
        ]
    )
    agent = build_chat_agent(
        model=model,
        tools=[risky_tool],
        middleware=[build_hitl_middleware(risky_tools={"risky_tool": ["approve"]})],
        checkpointer=checkpointer,
        use_model_call_limit=False,
    )
    config = {"configurable": {"thread_id": "hitl-recreate-p4"}}
    await agent.ainvoke({"messages": [HumanMessage(content="go")]}, config=config)

    # Recreate agent (runtime recreation) with the same saver.
    model2 = ScriptedChatModel(responses=[assistant("executed")])
    agent2 = build_chat_agent(
        model=model2,
        tools=[risky_tool],
        middleware=[build_hitl_middleware(risky_tools={"risky_tool": ["approve"]})],
        checkpointer=checkpointer,
        use_model_call_limit=False,
    )
    resume = await agent2.ainvoke(Command(resume=approve_all(1)), config=config)
    tool_msgs = [m for m in resume["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs
    assert "done:x" in tool_msgs[0].content


def test_revalidate_on_resume_blocks_stale_names() -> None:
    from tianzhou_agent_platform.services.agent_integration.scope import (
        revalidate_permissions_on_resume,
    )

    permitted = revalidate_permissions_on_resume(
        ["risky_tool", "revoked_tool"],
        allowed_names={"risky_tool"},
    )
    assert permitted == ["risky_tool"]
