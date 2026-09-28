"""Archive-before-compaction: the transcript hook and turn-retaining summarization of the runtime."""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.core.agent_runtime import build_agent
from tianzhou_agent_platform.core.agent_runtime.middleware.summarization import TurnSummarizationMiddleware
from tianzhou_agent_platform.core.agent_runtime.middleware.transcript import TranscriptHookMiddleware
from tianzhou_agent_platform.services.agent_integration.context import count_message_tokens
from tianzhou_agent_platform.services.agent_integration.history import TranscriptArchiver
from tests.support.fake_chat_model import ScriptedChatModel, assistant


OLD_QUESTION = "old question " * 40
OLD_ANSWER = "old answer " * 40


class RecordingConversations:
    def __init__(self) -> None:
        self.archived: list[Any] = []

    async def append_messages_idempotent(self, conversation_id: str, messages: list[Any]) -> None:
        self.archived.extend(messages)


class RecordingObserver:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def started(self, **details: Any) -> None:
        self.events.append(("started", details))

    async def completed(self, **details: Any) -> None:
        self.events.append(("completed", details))

    async def failed(self, error: Exception, **details: Any) -> None:
        self.events.append(("failed", {"error": str(error), **details}))


def _archiver(conversations: RecordingConversations, archived_ids: list[str] = []) -> TranscriptArchiver:  # noqa: B006
    return TranscriptArchiver(conversations, conversation_id="conv1", trace_id="trace_1", archived_ids=archived_ids)


async def test_archiver_appends_each_message_once_in_the_archive_format() -> None:
    conversations = RecordingConversations()
    archiver = _archiver(conversations, archived_ids=["seeded"])
    widget = WidgetDefinition(id="w1", kind="markdown", title="Done", markdown="# Done")
    messages = [
        HumanMessage(content="seeded", id="seeded"),
        HumanMessage(content="summary", id="s", additional_kwargs={"lc_source": "summarization"}),
        AIMessage(content="", id="a1", tool_calls=[{"id": "c1", "name": "demo", "args": {"x": 1}}]),
        ToolMessage(content='{"ok": true}', tool_call_id="c1", name="demo", id="t1"),
        AIMessage(content="done", id="a2"),
    ]

    await archiver.archive(messages, widgets={"a2": [widget]})
    await archiver.archive(messages)

    assert [record.id for record in conversations.archived] == ["a1", "t1", "a2"]
    call, result, answer = conversations.archived
    assert call.tool_calls == [
        {"id": "c1", "type": "function", "function": {"name": "demo", "arguments": '{"x": 1}'}}
    ]
    assert result.role == "tool" and result.content_type == "tool" and result.tool_call_id == "c1"
    assert answer.widgets == [widget] and answer.content_type == "widget"
    assert all(record.trace_id == "trace_1" for record in conversations.archived)


async def test_originals_are_archived_before_summarization_replaces_them() -> None:
    conversations = RecordingConversations()
    archiver = _archiver(conversations)
    model = ScriptedChatModel(responses=[assistant("summary of the old turn"), assistant("answer")])
    observer = RecordingObserver()
    summarization = TurnSummarizationMiddleware(
        model,
        threshold_tokens=1,
        min_messages=2,
        keep_turns=1,
        max_summary_input_tokens=3_072,
        token_counter=count_message_tokens,
        observer=observer,
    )
    agent = build_agent(model=model, middleware=[TranscriptHookMiddleware(archiver.archive), summarization])

    result = await agent.ainvoke(
        {
            "messages": [
                HumanMessage(content=OLD_QUESTION, id="h1"),
                AIMessage(content=OLD_ANSWER, id="a1"),
                HumanMessage(content="new question", id="h2"),
            ]
        }
    )

    working = [message.content for message in result["messages"]]
    assert OLD_QUESTION not in working and "new question" in working
    assert any("summary of the old turn" in str(content) for content in working)
    assert [record.content for record in conversations.archived] == [OLD_QUESTION, OLD_ANSWER, "new question"]
    assert [kind for kind, _ in observer.events] == ["started", "completed"]
    assert observer.events[1][1]["summarized_message_count"] == 2
    assert observer.events[1][1]["before_tokens"] > observer.events[1][1]["after_tokens"]
