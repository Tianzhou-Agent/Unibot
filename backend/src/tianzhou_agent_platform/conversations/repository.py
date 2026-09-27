"""Narrow persistence contracts consumed by ConversationService.

Protocols import this feature's data models, not its service. Concrete
repositories live in store/ and implement these structurally.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

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

    async def update_approval(self, approval_id: str, **fields: Any) -> ApprovalRecord | None: ...

    async def list_approvals(
        self, *, conversation_id: str, status: str | None = None
    ) -> list[ApprovalRecord]: ...
