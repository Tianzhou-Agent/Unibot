"""The agent core runs whole turns against in-memory stand-ins for its services, without the platform."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from tests.support.fake_chat_model import ScriptedChatModel, assistant, tool_calling
from unibot_agent.agent import Agent
from unibot_agent.capabilities import Capability, CapabilityScope
from unibot_agent.run import AgentRun
from unibot_agent.stop import TurnStop
from unibot_agent.types import ApprovalRequest, Message, ModelHandle, Session, TurnRequest


class MemorySessions:
    """A session archive and its approvals, kept in lists."""

    def __init__(self) -> None:
        self.messages: list[Message] = []
        self.approvals: list[ApprovalRequest] = []

    async def history(self, session: Session) -> list[Message]:
        return list(self.messages)

    async def append_messages(self, session_id: str, messages: Sequence[Message]) -> None:
        self.messages.extend(messages)

    async def take_external_messages(self, session_id: str) -> list[Message]:
        return []

    async def create(self, approval: ApprovalRequest) -> None:
        self.approvals.append(approval)

    def session(self) -> Session:
        return Session(id="s1", user_id="u1", tenant_id="t1", message_ids=frozenset(m.id for m in self.messages))


class OneModel:
    def __init__(self, model: ScriptedChatModel) -> None:
        self.model = model

    def resolve(self, run: AgentRun) -> ModelHandle:
        return ModelHandle(model=self.model, name="scripted", endpoint="memory://chat", context_window_tokens=32_000)


class Tools:
    """Every capability is always in scope; a call runs the handler registered under its name."""

    def __init__(self, capabilities: list[Capability], handlers: dict[str, Callable[..., Awaitable[Any]]]) -> None:
        self.capabilities = {item.function_name: item for item in capabilities}
        self.handlers = handlers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _scope(self) -> CapabilityScope:
        return CapabilityScope(visible=dict(self.capabilities), system_prompt="You are a test agent.")

    async def open_scope(self, run: AgentRun, session: Session, request: TurnRequest, **_: Any) -> CapabilityScope:
        return self._scope()

    async def resume_scope(self, run: AgentRun, session: Session, stored_scope_id: Any) -> CapabilityScope:
        return self._scope()

    async def execute(self, run: AgentRun, capability: Capability, call_id: str, arguments: dict[str, Any]) -> str:
        self.calls.append((capability.function_name, arguments))
        return json.dumps(await self.handlers[capability.function_name](**arguments))

    async def request_context(self, run: AgentRun) -> str:
        return ""


def _capability(name: str, *, requires_confirmation: bool = False) -> Capability:
    return Capability(
        kind="tool",
        capability_id=name,
        function_name=name,
        display_name=name.title(),
        description=f"The {name} tool.",
        input_schema={"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
        requires_confirmation=requires_confirmation,
        value=None,
    )


def _agent(model: ScriptedChatModel, sessions: MemorySessions, tools: Tools) -> Agent:
    return Agent(sessions=sessions, approvals=sessions, models=OneModel(model), capabilities=tools)


async def _weather(city: str) -> dict[str, Any]:
    return {"city": city, "forecast": "sunny"}


async def test_a_turn_calls_a_tool_and_archives_the_whole_exchange() -> None:
    sessions = MemorySessions()
    tools = Tools([_capability("weather")], {"weather": _weather})
    model = ScriptedChatModel(responses=[tool_calling("weather", {"city": "Paris"}), assistant("Sunny in Paris.")])

    result = await _agent(model, sessions, tools).run_turn(
        sessions.session(), TurnRequest(message="Weather in Paris?"), trace_id="trace_1"
    )

    assert (result.status, result.content, result.iterations) == ("completed", "Sunny in Paris.", 2)
    assert tools.calls == [("weather", {"city": "Paris"})]
    assert [message.role for message in sessions.messages] == ["user", "assistant", "tool", "assistant"]
    assert json.loads(sessions.messages[2].content) == {"city": "Paris", "forecast": "sunny"}
    assert result.message_id == sessions.messages[-1].id


async def test_a_risky_call_waits_for_approval_and_runs_once_approved() -> None:
    sessions = MemorySessions()
    tools = Tools([_capability("book", requires_confirmation=True)], {"book": _weather})
    model = ScriptedChatModel(responses=[tool_calling("book", {"city": "Rome"}), assistant("Booked.")])
    agent = _agent(model, sessions, tools)

    paused = await agent.run_turn(sessions.session(), TurnRequest(message="Book Rome"), trace_id="trace_1")

    assert paused.status == "approval_required" and tools.calls == []
    [approval] = sessions.approvals
    assert approval.capability_names == ["Book"] and paused.approval == approval

    continuation = await agent.open_approval(sessions.session(), approval, approve=True)
    resumed = await continuation.run()

    assert (resumed.status, resumed.content) == ("completed", "Booked.")
    assert tools.calls == [("book", {"city": "Rome"})]


async def test_a_stopped_turn_resumes_from_its_working_memory() -> None:
    sessions = MemorySessions()
    stop = TurnStop()

    async def slow(city: str) -> dict[str, Any]:
        stop.request()
        await asyncio.sleep(30)
        return {}

    tools = Tools([_capability("weather")], {"weather": slow})
    model = ScriptedChatModel(responses=[tool_calling("weather", {"city": "Oslo"}), assistant("It was interrupted.")])
    agent = _agent(model, sessions, tools)

    stopped = await agent.run_turn(sessions.session(), TurnRequest(message="Weather?"), trace_id="trace_1", stop=stop)
    assert stopped.status == "stopped"

    resumed = await agent.resume_turn(sessions.session(), trace_id="trace_2")

    # The interrupted call is closed, never run again; the model answers with what it has.
    assert (resumed.status, resumed.content) == ("completed", "It was interrupted.")
    assert len(tools.calls) == 1
