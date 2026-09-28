"""Narrow persistence contracts consumed by ConversationService.

Protocols import this feature's data models, not its service. Concrete
repositories live in store/ and implement these structurally.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tianzhou_agent_platform.conversations.models import (
    Conversation,
    ConversationCreate,
    ConversationUpdate,
    Message,
)
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord


@runtime_checkable
class ConversationRepository(Protocol):
    async def create_conversation(self, payload: ConversationCreate) -> Conversation: ...

    async def get_conversation(self, conversation_id: str) -> Conversation | None: ...

    async def update_conversation(
        self, conversation_id: str, payload: ConversationUpdate
    ) -> Conversation | None: ...

    async def delete_conversation(self, conversation_id: str) -> bool: ...

    async def list_conversations(
        self, *, user_id: str, tenant_id: str, workspace_id: str | None = None
    ) -> list[Conversation]: ...

    async def append_messages(self, conversation_id: str, messages: list[Message]) -> Conversation | None: ...


@runtime_checkable
class ApprovalRepository(Protocol):
    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord: ...

    async def get_approval(self, approval_id: str) -> ApprovalRecord | None: ...

    async def list_approvals(
        self, *, conversation_id: str, status: str | None = None
    ) -> list[ApprovalRecord]: ...


@runtime_checkable
class ConversationRunRepository(Protocol):
    """Actor checks, run leases and routing state of a conversation, consumed by the chat workflow."""

    async def require_conversation_actor(
        self, conversation_id: str, *, user_id: str, tenant_id: str
    ) -> Conversation: ...

    async def start_conversation_run(self, conversation_id: str, trace_id: str) -> Conversation: ...

    async def finish_conversation_run(
        self,
        conversation_id: str,
        *,
        status: str = "idle",
        error: str | None = None,
        expected_trace_id: str | None = None,
    ) -> Conversation: ...

    async def set_conversation_status(self, conversation_id: str, status: str) -> Conversation: ...

    async def bind_conversation_aina(
        self, conversation_id: str, aina_id: str, *, make_primary: bool = False, mark_used: bool = False
    ) -> Conversation: ...

    async def set_approval_status(self, approval_id: str, status: str) -> ApprovalRecord: ...

    async def cancel_pending_approvals(self, conversation_id: str) -> list[ApprovalRecord]: ...
