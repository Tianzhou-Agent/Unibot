"""Narrow persistence contracts consumed by ConversationService.

Protocols import this feature's data models, not its service. Concrete
repositories live in store/ and implement these structurally; lookups of a
missing record raise ``not_found`` rather than returning None.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol, runtime_checkable

from tianzhou_agent_platform.conversations.attachments import Attachment
from tianzhou_agent_platform.conversations.models import (
    Conversation,
    ConversationCreate,
    ConversationUpdate,
    Message,
)
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord
from tianzhou_agent_platform.core.workspace import Workspace


@runtime_checkable
class ConversationRepository(Protocol):
    async def create_conversation(self, data: ConversationCreate) -> Conversation: ...

    async def get_conversation(self, conversation_id: str, *, include_deleted: bool = False) -> Conversation: ...

    async def update_conversation(self, conversation_id: str, data: ConversationUpdate) -> Conversation: ...

    async def list_conversations(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        category: str | None = None,
        workspace_id: str | None = None,
    ) -> list[Conversation]: ...

    async def require_workspace_actor(self, workspace_id: str, *, user_id: str, tenant_id: str) -> Workspace: ...

    async def put_chat_attachment(self, attachment: Attachment) -> Attachment: ...

    async def get_chat_attachment(self, file_ref: str) -> Attachment | None: ...

    async def list_chat_attachments(self, file_refs: list[str]) -> list[Attachment]: ...

    async def bind_chat_attachment(
        self, file_ref: str, *, conversation_id: str, workspace_id: str | None
    ) -> Attachment | None: ...

    async def delete_chat_attachment(self, file_ref: str) -> None: ...

    async def list_unsent_chat_attachments(
        self, *, tenant_id: str, user_id: str, created_before: datetime
    ) -> list[Attachment]: ...

    async def append_messages(
        self, conversation_id: str, messages: Iterable[Message]
    ) -> Conversation | None: ...


@runtime_checkable
class ApprovalRepository(Protocol):
    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord: ...

    async def get_approval(self, approval_id: str) -> ApprovalRecord: ...

    async def list_approvals(
        self,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        status: str | None = None,
    ) -> list[ApprovalRecord]: ...


@runtime_checkable
class ConversationRunRepository(Protocol):
    """Actor checks, run leases and routing state of a conversation, consumed by the chat workflow."""

    async def require_conversation_actor(
        self, conversation_id: str, *, user_id: str, tenant_id: str, include_deleted: bool = False
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

    async def reconcile_conversation_run(self, conversation_id: str) -> Conversation: ...

    async def list_interrupted_conversation_runs(self, *, since: datetime) -> list[Conversation]: ...

    async def claim_conversation_run_recovery(self, conversation_id: str) -> bool: ...

    async def set_conversation_status(self, conversation_id: str, status: str) -> Conversation: ...

    async def bind_conversation_aina(
        self, conversation_id: str, aina_id: str, *, make_primary: bool = False, mark_used: bool = False
    ) -> Conversation: ...

    async def set_approval_status(self, approval_id: str, status: str) -> ApprovalRecord: ...

    async def cancel_pending_approvals(self, conversation_id: str) -> list[ApprovalRecord]: ...
