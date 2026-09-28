"""Runtime middleware composed with scripted native models, without any Unibot service."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from tianzhou_agent_platform.core.agent_runtime import build_agent
from tianzhou_agent_platform.core.agent_runtime.middleware.approval_policy import TerminalDenialMiddleware
from tianzhou_agent_platform.core.agent_runtime.middleware.provider_policy import (
    ForcedToolChoiceMiddleware,
    ProviderErrorMiddleware,
)
from tianzhou_agent_platform.core.agent_runtime.middleware.summarization import (
    TurnSummarizationMiddleware,
    is_summary_message,
)
from tianzhou_agent_platform.core.agent_runtime.middleware.transcript import TranscriptHookMiddleware
from tests.support.fake_chat_model import ScriptedChatModel, assistant, tool_calling


def _tokens(messages: Any) -> int:
    return sum(len(str(message.content)) for message in messages)


class ProviderRejection(Exception):
    pass


class ToolChoiceRejection(Exception):
    pass


class RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def started(self, **details: Any) -> None:
        self.events.append(("started", details))

    async def completed(self, **details: Any) -> None:
        self.events.append(("completed", details))

    async def failed(self, error: Exception, **details: Any) -> None:
        self.events.append(("failed", {"error": str(error), **details}))


async def test_provider_errors_are_translated_by_the_injected_mapping() -> None:
    def fail(**_: Any) -> AIMessage:
        raise ProviderRejection("HTTP 401")

    def mapped(exc: Exception) -> Exception | None:
        return RuntimeError(f"mapped: {exc}") if isinstance(exc, ProviderRejection) else None

    agent = build_agent(model=ScriptedChatModel(responses=[fail]), middleware=[ProviderErrorMiddleware(mapped)])

    with pytest.raises(RuntimeError, match="mapped: HTTP 401") as raised:
        await agent.ainvoke({"messages": [HumanMessage(content="hi")]})
    assert isinstance(raised.value.__cause__, ProviderRejection)


async def test_forced_tool_choice_is_retried_without_it_when_rejected() -> None:
    @tool
    def lookup() -> str:
        """Look something up."""
        return "found"

    class ForceLookup(ForcedToolChoiceMiddleware):
        async def awrap_model_call(self, request: Any, handler: Any) -> Any:
            if not any(isinstance(message, ToolMessage) for message in request.messages):
                request = request.override(tool_choice="lookup", system_message=None)
            return await super().awrap_model_call(request, handler)

    def reject(**_: Any) -> AIMessage:
        raise ToolChoiceRejection("tool_choice unsupported")

    model = ScriptedChatModel(responses=[reject, tool_calling("lookup", {}), assistant("done")])
    agent = build_agent(
        model=model,
        tools=[lookup],
        middleware=[ForceLookup(lambda exc: isinstance(exc, ToolChoiceRejection))],
    )

    result = await agent.ainvoke({"messages": [HumanMessage(content="find it")]})

    assert result["messages"][-1].content == "done"
    first, second, _ = model.calls
    assert first["tool_choice"] == "lookup"
    assert second["tool_choice"] is None
    assert second["messages"][0].content.endswith("Call that function before answering the user.")


async def test_terminal_denial_closes_every_call_and_skips_the_model() -> None:
    from langchain.agents.middleware import HumanInTheLoopMiddleware
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command

    executed: list[str] = []

    @tool
    def risky() -> str:
        """Needs approval."""
        executed.append("risky")
        return "done"

    @tool
    def sibling() -> str:
        """Proposed in the same response as the risky call."""
        executed.append("sibling")
        return "done"

    def agent(model: ScriptedChatModel, *, denied: bool) -> Any:
        return build_agent(
            model=model,
            tools=[risky, sibling],
            middleware=[
                TerminalDenialMiddleware(denied=denied, tool_result="denied", reply="cancelled"),
                HumanInTheLoopMiddleware({"risky": {"allowed_decisions": ["approve", "reject"]}}),
            ],
            checkpointer=saver,
        )

    saver = InMemorySaver()
    config = {"configurable": {"thread_id": "t"}}
    batch = AIMessage(
        content="",
        tool_calls=[{"name": "sibling", "args": {}, "id": "s1"}, {"name": "risky", "args": {}, "id": "r1"}],
    )
    await agent(ScriptedChatModel(responses=[batch]), denied=False).ainvoke(
        {"messages": [HumanMessage(content="go")]}, config=config
    )
    model = ScriptedChatModel(responses=[])
    result = await agent(model, denied=True).ainvoke(
        Command(resume={"decisions": [{"type": "reject"}]}), config=config
    )

    assert executed == [] and model.calls == []
    results = {m.tool_call_id: m.content for m in result["messages"] if isinstance(m, ToolMessage)}
    assert results["s1"] == "denied" and "rejected" in results["r1"]
    assert result["messages"][-1].content == "cancelled"


async def test_transcript_hook_sees_every_message_before_each_model_call() -> None:
    seen: list[list[str]] = []

    async def persist(messages: list[Any]) -> None:
        seen.append([str(message.content) for message in messages])

    @tool
    def echo(text: str) -> str:
        """Echo."""
        return text

    model = ScriptedChatModel(responses=[tool_calling("echo", {"text": "result"}), assistant("done")])
    await build_agent(model=model, tools=[echo], middleware=[TranscriptHookMiddleware(persist)]).ainvoke(
        {"messages": [HumanMessage(content="go")]}
    )

    assert seen == [["go"], ["go", "", "result"]]


def _summarization(model: ScriptedChatModel, observer: RecordingObserver, **overrides: Any) -> TurnSummarizationMiddleware:
    options: dict[str, Any] = {
        "threshold_tokens": 1,
        "min_messages": 2,
        "keep_turns": 1,
        "max_summary_input_tokens": 10_000,
        "token_counter": _tokens,
        "observer": observer,
    }
    options.update(overrides)
    return TurnSummarizationMiddleware(model, **options)


def test_summarization_keeps_complete_recent_turns() -> None:
    middleware = _summarization(ScriptedChatModel(responses=[]), RecordingObserver())
    messages = [
        HumanMessage(content="summary", additional_kwargs={"lc_source": "summarization"}),
        HumanMessage(content="old question"),
        AIMessage(content="old answer"),
        HumanMessage(content="run the tool"),
        AIMessage(content="", tool_calls=[{"id": "call_1", "name": "demo", "args": {}}]),
        ToolMessage(content="result", tool_call_id="call_1", name="demo"),
        AIMessage(content="tool answer"),
    ]

    # Everything before the latest user turn is summarized; the turn's tool group stays whole.
    assert middleware._determine_cutoff_index(messages) == 3
    assert middleware._determine_cutoff_index(messages[:3]) == 0
    assert is_summary_message(messages[0]) and not is_summary_message(messages[1])


OLD_TURN = [HumanMessage(content="old question " * 20), AIMessage(content="old answer " * 20)]


async def test_summary_replaces_old_turns_in_working_memory() -> None:
    observer = RecordingObserver()
    model = ScriptedChatModel(responses=[assistant("short summary"), assistant("answer")])
    result = await build_agent(model=model, middleware=[_summarization(model, observer)]).ainvoke(
        {"messages": [*OLD_TURN, HumanMessage(content="new question")]}
    )

    assert [str(m.content) for m in result["messages"][1:]] == ["new question", "answer"]
    assert is_summary_message(result["messages"][0]) and "short summary" in result["messages"][0].content
    assert [kind for kind, _ in observer.events] == ["started", "completed"]


@pytest.mark.parametrize(
    ("summary", "budget", "error"),
    [
        (assistant(""), 10_000, "empty summary"),
        (assistant("summary " * 200), 10_000, "did not reduce"),
        # An oversized summary request is never sent.
        (None, 5, "exceeds the compression model input budget"),
    ],
)
async def test_failed_summary_keeps_the_full_history(summary: AIMessage | None, budget: int, error: str) -> None:
    observer = RecordingObserver()
    model = ScriptedChatModel(responses=[*([summary] if summary else []), assistant("answer")])
    result = await build_agent(
        model=model, middleware=[_summarization(model, observer, max_summary_input_tokens=budget)]
    ).ainvoke({"messages": [*OLD_TURN, HumanMessage(content="new question")]})

    assert [m.content for m in result["messages"][:2]] == [m.content for m in OLD_TURN]
    assert result["messages"][-1].content == "answer"
    assert observer.events[-1][0] == "failed" and error in observer.events[-1][1]["error"]
