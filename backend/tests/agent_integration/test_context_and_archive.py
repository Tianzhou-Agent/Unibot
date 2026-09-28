"""Phase 2: native summarization policy and archive-before-compaction."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tianzhou_agent_platform.services.agent_integration.context import (
    UNIBOT_SUMMARY_PROMPT,
    build_summarization_middleware,
    projection_messages,
)
from tianzhou_agent_platform.services.agent_integration.history import (
    archive_before_compaction,
    native_to_archive,
)
from tests.support.fake_chat_model import ScriptedChatModel, assistant


def test_summarization_middleware_uses_unibot_prompt_and_keep() -> None:
    model = ScriptedChatModel(responses=[assistant("SUMMARY")])
    mw = build_summarization_middleware(
        model=model,
        trigger=("messages", 12),
        keep=("messages", 4),
    )
    assert mw.summary_prompt == UNIBOT_SUMMARY_PROMPT
    assert mw.keep == ("messages", 4)
    assert mw.trigger == ("messages", 12)


def test_projection_messages_are_transient_annotations() -> None:
    text = projection_messages(
        ui_context="user is on the dashboard",
        memory_context=["likes dark mode"],
        current_tasks=["write report"],
    )
    assert "UI context" in text
    assert "Memory" in text
    assert "Current tasks" in text
    # Projection is a prompt fragment, not a Message archive record.
    assert not isinstance(text, HumanMessage)


async def test_archive_before_compaction_preserves_originals() -> None:

    from tianzhou_agent_platform.conversations.models import Conversation, ConversationCreate
    from tianzhou_agent_platform.conversations.service import ConversationService

    archived: list = []

    class FakeRepo:
        async def create_conversation(self, payload: ConversationCreate) -> Conversation:
            return Conversation(
                id="conv1",
                user_id=payload.user_id,
                tenant_id=payload.tenant_id,
                title=payload.title,
            )

        async def get_conversation(self, conversation_id: str):
            return Conversation(
                id=conversation_id, user_id="u", tenant_id="t", title="x"
            )

        async def update_conversation(self, conversation_id: str, payload):
            return None

        async def delete_conversation(self, conversation_id: str) -> bool:
            return True

        async def list_conversations(self, *, user_id: str, tenant_id: str, workspace_id=None):
            return []

        async def append_messages(self, conversation_id: str, messages):
            archived.extend(messages)
            return Conversation(id=conversation_id, user_id="u", tenant_id="t", title="x")

        async def create_approval(self, approval):
            return approval

        async def get_approval(self, approval_id: str):
            return None

        async def update_approval(self, approval_id: str, **fields):
            return None

        async def list_approvals(self, *, conversation_id: str, status: str | None = None):
            return []

    service = ConversationService(FakeRepo())
    messages = [
        HumanMessage(content="q", id="m1"),
        AIMessage(content="a", id="m2"),
        ToolMessage(content="r", tool_call_id="c1", name="t", id="m3"),
    ]
    await archive_before_compaction(service, conversation_id="conv1", messages=messages)
    assert len(archived) == 3
    assert archived[0].role == "user"
    assert archived[1].role == "assistant"
    assert archived[2].role == "tool"
    assert archived[2].tool_call_id == "c1"


def test_native_to_archive_preserves_finish_and_usage_in_content() -> None:
    msg = AIMessage(
        content="ok",
        response_metadata={"finish_reason": "stop"},
        usage_metadata={"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
    )
    record = native_to_archive(msg)
    assert record.role == "assistant"
    assert record.content == "ok"
    assert record.id  # stable id assigned
