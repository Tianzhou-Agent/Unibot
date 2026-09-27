"""Phase 1B: native models, history conversion and one-tool ChatService path."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tianzhou_agent_platform.conversations.models import Message
from tianzhou_agent_platform.model_providers.factory import (
    create_model_from_settings,
    create_native_chat_model,
)
from tianzhou_agent_platform.model_providers.models import ModelRuntimeConfig, use_model_runtime
from tianzhou_agent_platform.services.agent_integration.builder import build_chat_agent
from tianzhou_agent_platform.services.agent_integration.history import (
    archive_to_native,
    legacy_history_to_native,
    native_to_archive,
    new_turn_inputs,
)
from tests.support.fake_chat_model import ScriptedChatModel, assistant, tool_calling


def test_native_model_factory_rejects_unconfigured_provider() -> None:
    from tianzhou_agent_platform.core.errors import PlatformError

    with pytest.raises(PlatformError):
        create_native_chat_model(model="", api_key="", base_url="")


def test_native_model_factory_builds_chat_openai() -> None:
    model = create_native_chat_model(
        model="gpt-test",
        api_key="sk-test",
        base_url="https://api.example.com/v1",
        timeout_seconds=30.0,
        max_retries=0,
    )
    assert type(model).__name__ == "ChatOpenAI"
    # No accidental SDK retries
    assert model.max_retries == 0


def test_native_model_from_settings() -> None:
    model = create_model_from_settings(
        model="gpt-test",
        api_key="sk-test",
        base_url="https://api.example.com/v1",
    )
    assert model is not None


def test_archive_to_native_roundtrip_preserves_ids_and_tool_pairs() -> None:
    user = Message(id="m1", role="user", content="hello")
    ai = Message(
        id="m2",
        role="assistant",
        content="",
        tool_calls=[{"name": "echo_tool", "args": {"text": "hi"}, "id": "c1"}],
    )
    tool = Message(id="m3", role="tool", content="echo:hi", tool_call_id="c1", name="echo_tool")

    n_user = archive_to_native(user)
    n_ai = archive_to_native(ai)
    n_tool = archive_to_native(tool)

    assert isinstance(n_user, HumanMessage) and n_user.id == "m1"
    assert isinstance(n_ai, AIMessage) and n_ai.tool_calls[0]["id"] == "c1"
    assert isinstance(n_tool, ToolMessage) and n_tool.tool_call_id == "c1"

    back_user = native_to_archive(n_user)
    assert back_user.id == "m1" and back_user.role == "user"
    back_tool = native_to_archive(n_tool)
    assert back_tool.tool_call_id == "c1" and back_tool.name == "echo_tool"


def test_legacy_history_import_converts_all_roles() -> None:
    messages = [
        Message(id="a", role="user", content="q"),
        Message(id="b", role="assistant", content="a"),
    ]
    native = legacy_history_to_native(messages)
    assert len(native) == 2
    assert isinstance(native[0], HumanMessage)
    assert isinstance(native[1], AIMessage)


def test_new_turn_inputs_only_submits_new_user_text() -> None:
    inputs = new_turn_inputs("next question")
    assert len(inputs) == 1
    assert isinstance(inputs[0], HumanMessage)
    assert inputs[0].content == "next question"


async def test_builder_one_tool_native_path() -> None:
    from tianzhou_agent_platform.services.agent_integration.tools import bind_echo_tool

    model = ScriptedChatModel(
        responses=[
            tool_calling("echo_tool", {"text": "hi"}, call_id="c1"),
            assistant("done"),
        ]
    )
    agent = build_chat_agent(model=model, tools=[bind_echo_tool()], system_prompt="test")
    result = await agent.ainvoke({"messages": [HumanMessage(content="echo hi")]})
    tool_msgs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
    assert tool_msgs and tool_msgs[0].content == "echo:hi"
    assert result["messages"][-1].content == "done"


async def test_chat_service_native_runner_path() -> None:
    from uuid import uuid4

    from tianzhou_agent_platform.conversations.models import Conversation, ConversationCreate
    from tianzhou_agent_platform.conversations.schemas import ChatRequest, ChatResponse
    from tianzhou_agent_platform.conversations.service import ConversationService
    from tianzhou_agent_platform.services.chat import ChatService

    class FakeRepo:
        def __init__(self) -> None:
            self.store: dict[str, Conversation] = {}

        async def create_conversation(self, payload: ConversationCreate) -> Conversation:
            conv = Conversation(
                id=f"conv_{uuid4().hex}",
                user_id=payload.user_id,
                tenant_id=payload.tenant_id,
                title=payload.title,
            )
            self.store[conv.id] = conv
            return conv

        async def get_conversation(self, conversation_id: str):
            return self.store.get(conversation_id)

        async def update_conversation(self, conversation_id: str, payload):
            return self.store.get(conversation_id)

        async def delete_conversation(self, conversation_id: str) -> bool:
            return self.store.pop(conversation_id, None) is not None

        async def list_conversations(self, *, user_id: str, tenant_id: str, workspace_id=None):
            return list(self.store.values())

        async def append_messages(self, conversation_id: str, messages):
            return self.store.get(conversation_id)

        async def create_approval(self, approval):
            return approval

        async def get_approval(self, approval_id: str):
            return None

        async def update_approval(self, approval_id: str, **fields):
            return None

        async def list_approvals(self, *, conversation_id: str, status: str | None = None):
            return []

    class NativeRunner:
        async def run_turn(self, request: ChatRequest, conversation: Conversation) -> ChatResponse:
            return ChatResponse(
                conversation_id=conversation.id,
                message_id="m1",
                content="native ok",
                status="completed",
                trace_id="tr1",
                iterations=1,
            )

        async def confirm(self, conversation_id: str, approval_id: str, action: Any) -> ChatResponse:
            raise NotImplementedError

        async def deny(self, conversation_id: str, approval_id: str, action: Any) -> ChatResponse:
            raise NotImplementedError

    service = ChatService(ConversationService(FakeRepo()), native_runner=NativeRunner())
    response = await service.run_turn(ChatRequest(message="hi", user_id="u1", tenant_id="t1"))
    assert response.status == "completed"
    assert response.content == "native ok"


def test_model_runtime_context_is_actor_scoped() -> None:
    config = ModelRuntimeConfig(
        provider_id="p1",
        provider_name="Test",
        base_url="https://api.example.com/v1",
        api_key="sk-x",
        model_id="m1",
        model_name="GPT",
        model="gpt-test",
        context_window_tokens=32_000,
        timeout_seconds=20.0,
    )
    with use_model_runtime(config):
        from tianzhou_agent_platform.model_providers.models import (
            current_context_window_tokens,
            current_model_runtime,
        )

        assert current_model_runtime() is config
        assert current_context_window_tokens(128_000) == 32_000
    from tianzhou_agent_platform.model_providers.models import current_model_runtime

    assert current_model_runtime() is None
