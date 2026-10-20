"""Conversation/approval persistence, ownership and run lifecycle.

Owns archive operations and run-generation/lease helpers. Does not invoke
the agent or import ChatService.
"""

from __future__ import annotations


from tianzhou_agent_platform.conversations.models import (
    Conversation,
    ConversationCreate,
    ConversationUpdate,
    Message,
)
from tianzhou_agent_platform.conversations.repository import (
    ApprovalRepository,
    ConversationRepository,
    ConversationRunRepository,
)
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord
from tianzhou_agent_platform.core.errors import PlatformError, not_found


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository & ApprovalRepository & ConversationRunRepository,
    ) -> None:
        self._repo = repository

    async def create(self, payload: ConversationCreate) -> Conversation:
        return await self._repo.create_conversation(payload)

    async def get(self, conversation_id: str, *, include_deleted: bool = False) -> Conversation:
        return await self._repo.get_conversation(conversation_id, include_deleted=include_deleted)

    async def get_reconciled(self, conversation_id: str) -> Conversation:
        """The conversation, with a run whose owner is gone marked as finished."""
        return await self._repo.reconcile_conversation_run(conversation_id)

    async def update(self, conversation_id: str, payload: ConversationUpdate) -> Conversation:
        return await self._repo.update_conversation(conversation_id, payload)

    async def restore(self, conversation_id: str) -> Conversation:
        return await self._repo.set_conversation_status(conversation_id, "active")

    async def list_for_actor(
        self,
        *,
        user_id: str,
        tenant_id: str,
        category: str | None = None,
        workspace_id: str | None = None,
    ) -> list[Conversation]:
        if workspace_id is not None:
            await self._repo.require_workspace_actor(workspace_id, user_id=user_id, tenant_id=tenant_id)
        return await self._repo.list_conversations(
            user_id=user_id, tenant_id=tenant_id, category=category, workspace_id=workspace_id
        )

    async def list_approvals(
        self,
        *,
        user_id: str,
        tenant_id: str,
        conversation_id: str | None = None,
        status: str | None = None,
    ) -> list[ApprovalRecord]:
        return await self._repo.list_approvals(
            conversation_id=conversation_id, user_id=user_id, tenant_id=tenant_id, status=status
        )

    def ensure_ownership(self, conversation: Conversation, *, user_id: str, tenant_id: str) -> None:
        if conversation.user_id != user_id or conversation.tenant_id != tenant_id:
            raise PlatformError(
                "PERMISSION_DENIED",
                "Conversation ownership does not match the authenticated user",
                status_code=403,
                source="auth",
                user_message="无权访问该对话。",
            )

    async def append_messages_idempotent(
        self, conversation_id: str, messages: list[Message]
    ) -> Conversation:
        """Append archive messages. Callers supply stable message IDs for idempotency."""
        conversation = await self._repo.append_messages(conversation_id, messages)
        if conversation is None:
            raise not_found("conversation", conversation_id)
        return conversation

    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        return await self._repo.create_approval(approval)

    async def get_approval(self, approval_id: str) -> ApprovalRecord:
        return await self._repo.get_approval(approval_id)

    async def require_for_actor(self, conversation_id: str, *, user_id: str, tenant_id: str) -> Conversation:
        return await self._repo.require_conversation_actor(conversation_id, user_id=user_id, tenant_id=tenant_id)

    async def start_run(self, conversation_id: str, trace_id: str) -> Conversation:
        """Take the conversation's run lease; fails with CONFLICT while another run holds it."""
        return await self._repo.start_conversation_run(conversation_id, trace_id)

    async def finish_run(
        self,
        conversation_id: str,
        *,
        status: str = "idle",
        error: str | None = None,
        expected_trace_id: str | None = None,
    ) -> Conversation:
        """Release the run lease (a no-op when ``expected_trace_id`` no longer holds it)."""
        return await self._repo.finish_conversation_run(
            conversation_id,
            status=status,
            error=error,
            expected_trace_id=expected_trace_id,
        )

    async def mark_deleted(self, conversation_id: str) -> Conversation:
        return await self._repo.set_conversation_status(conversation_id, "deleted")

    async def bind_aina(self, conversation_id: str, aina_id: str) -> Conversation:
        """Record that the conversation routed to an AINA (it becomes the last used one)."""
        return await self._repo.bind_conversation_aina(conversation_id, aina_id, mark_used=True)

    async def set_approval_status(self, approval_id: str, status: str) -> ApprovalRecord:
        return await self._repo.set_approval_status(approval_id, status)

    async def cancel_pending_approvals(self, conversation_id: str) -> list[ApprovalRecord]:
        """Deny the conversation's pending approvals; returns those that belong to a recorded trace."""
        return await self._repo.cancel_pending_approvals(conversation_id)
