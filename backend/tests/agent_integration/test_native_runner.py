"""Phase 6: native runner turn/confirm/deny and factory quirks."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import ToolRuntime

from tianzhou_agent_platform.conversations.models import Conversation, ConversationCreate
from tianzhou_agent_platform.conversations.schemas import ApprovalAction, ChatRequest
from tianzhou_agent_platform.conversations.service import ConversationService
from tianzhou_agent_platform.model_providers.factory import (
    create_native_chat_model,
    _openai_base_url,
)
from tianzhou_agent_platform.services.agent_integration.runner import (
    NativeAgentRunner,
    thread_id_for,
)
from tests.support.fake_chat_model import ScriptedChatModel, assistant, tool_calling


@tool
def echo_tool(text: str, runtime: ToolRuntime) -> str:
    """Echo text."""
    return f"echo:{text}"


@tool
def risky_tool(action: str, runtime: ToolRuntime) -> str:
    """Requires approval."""
    return f"done:{action}"


class FakeRepo:
    def __init__(self) -> None:
        self.convs: dict[str, Conversation] = {}
        self.approvals: dict[str, Any] = {}
        self.archived: list = []

    async def create_conversation(self, payload: ConversationCreate) -> Conversation:
        conv = Conversation(
            id=f"conv_{uuid4().hex}",
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
            title=payload.title,
        )
        self.convs[conv.id] = conv
        return conv

    async def get_conversation(self, conversation_id: str):
        return self.convs.get(conversation_id)

    async def update_conversation(self, conversation_id: str, payload):
        return self.convs.get(conversation_id)

    async def delete_conversation(self, conversation_id: str) -> bool:
        return self.convs.pop(conversation_id, None) is not None

    async def list_conversations(self, *, user_id: str, tenant_id: str, workspace_id=None):
        return list(self.convs.values())

    async def append_messages(self, conversation_id: str, messages):
        self.archived.extend(messages)
        return self.convs.get(conversation_id)

    async def create_approval(self, approval):
        self.approvals[approval.id] = approval
        return approval

    async def get_approval(self, approval_id: str):
        return self.approvals.get(approval_id)

    async def update_approval(self, approval_id: str, **fields):
        approval = self.approvals.get(approval_id)
        if approval is None:
            return None
        updated = approval.model_copy(update=fields)
        self.approvals[approval_id] = updated
        return updated

    async def list_approvals(self, *, conversation_id: str, status: str | None = None):
        items = [a for a in self.approvals.values() if a.conversation_id == conversation_id]
        if status:
            items = [a for a in items if a.status == status]
        return items


def test_thread_id_scheme() -> None:
    assert thread_id_for("c1") == "lc-v2:c1"


def test_openai_base_url_strips_chat_completions() -> None:
    assert _openai_base_url("https://api.example.com/v1/chat/completions") == "https://api.example.com/v1"
    assert _openai_base_url("https://api.example.com/v1") == "https://api.example.com/v1"


def test_native_factory_preserves_provider_quirks() -> None:
    model = create_native_chat_model(
        model="gpt-test",
        api_key="sk",
        base_url="https://api.example.com/v1/chat/completions",
        max_retries=0,
    )
    assert model.max_retries == 0
    assert "chat/completions" not in str(model.openai_api_base)


async def test_native_runner_plain_turn() -> None:
    repo = FakeRepo()
    conversations = ConversationService(repo)
    model = ScriptedChatModel(responses=[assistant("hello there")])
    runner = NativeAgentRunner(
        model=model,
        conversations=conversations,
        checkpointer=InMemorySaver(),
        tools_provider=lambda req: [echo_tool],
        model_run_limit=4,
    )
    request = ChatRequest(message="hi", user_id="u1", tenant_id="t1")
    conversation = await conversations.create(
        ConversationCreate(user_id="u1", tenant_id="t1", title="t")
    )
    response = await runner.run_turn(request, conversation)
    assert response.status == "completed"
    assert response.content == "hello there"
    assert response.conversation_id == conversation.id
    # User input was archived.
    assert any(m.role == "user" for m in repo.archived)


async def test_native_runner_tool_turn_archives_tool_result() -> None:
    repo = FakeRepo()
    conversations = ConversationService(repo)
    model = ScriptedChatModel(
        responses=[
            tool_calling("echo_tool", {"text": "hi"}, call_id="c1"),
            assistant("done"),
        ]
    )
    runner = NativeAgentRunner(
        model=model,
        conversations=conversations,
        checkpointer=InMemorySaver(),
        tools_provider=lambda req: [echo_tool],
        model_run_limit=4,
    )
    conversation = await conversations.create(
        ConversationCreate(user_id="u1", tenant_id="t1", title="t")
    )
    response = await runner.run_turn(
        ChatRequest(message="echo hi", user_id="u1", tenant_id="t1"), conversation
    )
    assert response.status == "completed"
    roles = [m.role for m in repo.archived]
    assert "tool" in roles
    assert any(m.content == "echo:hi" for m in repo.archived if m.role == "tool")


async def test_native_runner_confirm_resumes_risky_tool() -> None:
    repo = FakeRepo()
    conversations = ConversationService(repo)
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(
        responses=[
            tool_calling("risky_tool", {"action": "send"}, call_id="r1"),
            assistant("executed"),
        ]
    )
    runner = NativeAgentRunner(
        model=model,
        conversations=conversations,
        checkpointer=checkpointer,
        tools_provider=lambda req: [risky_tool],
        risky_tools={"risky_tool": ["approve", "reject"]},
        model_run_limit=4,
    )
    conversation = await conversations.create(
        ConversationCreate(user_id="u1", tenant_id="t1", title="t")
    )
    response = await runner.run_turn(
        ChatRequest(message="send it", user_id="u1", tenant_id="t1"), conversation
    )
    assert response.status == "approval_required"
    assert response.approval is not None
    assert response.approval.runtime_ref is not None
    assert response.approval.runtime_ref["thread_id"] == "lc-v2:" + conversation.id

    # Approve — original call resumes and executes.
    # Need a second response for post-resume model call if any.
    model.responses.append(assistant("executed"))
    confirm = await runner.confirm(
        conversation.id, response.approval.id, ApprovalAction(user_id="u1", tenant_id="t1")
    )
    assert confirm.status == "completed"
    # Tool should have run after resume.
    assert any(m.role == "tool" and "done:send" in m.content for m in repo.archived)


async def test_native_runner_deny_skips_side_effect() -> None:
    repo = FakeRepo()
    conversations = ConversationService(repo)
    checkpointer = InMemorySaver()
    model = ScriptedChatModel(
        responses=[
            tool_calling("risky_tool", {"action": "pay"}, call_id="r1"),
            assistant("cancelled"),
        ]
    )
    runner = NativeAgentRunner(
        model=model,
        conversations=conversations,
        checkpointer=checkpointer,
        tools_provider=lambda req: [risky_tool],
        risky_tools={"risky_tool": ["approve", "reject"]},
        model_run_limit=4,
    )
    conversation = await conversations.create(
        ConversationCreate(user_id="u1", tenant_id="t1", title="t")
    )
    response = await runner.run_turn(
        ChatRequest(message="pay", user_id="u1", tenant_id="t1"), conversation
    )
    assert response.status == "approval_required"
    model.responses.append(assistant("cancelled"))
    await runner.deny(
        conversation.id, response.approval.id, ApprovalAction(user_id="u1", tenant_id="t1")
    )
    assert not any(m.role == "tool" and "done:pay" in m.content for m in repo.archived)
