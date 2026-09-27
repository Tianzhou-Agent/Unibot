"""Conversation/approval persistence, ownership and run lifecycle.

Owns archive operations and run-generation/lease helpers. Does not invoke
the agent or import ChatService.
"""

from __future__ import annotations

from typing import Any

from tianzhou_agent_platform.conversations.models import (
    Conversation,
    ConversationCreate,
    ConversationUpdate,
    Message,
)
from tianzhou_agent_platform.conversations.repository import (
    ApprovalRepository,
    ConversationRepository,
)
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord
from tianzhou_agent_platform.core.errors import PlatformError, conflict, not_found


class ConversationService:
    def __init__(
        self,
        repository: ConversationRepository & ApprovalRepository,
    ) -> None:
        self._repo = repository

    async def create(self, payload: ConversationCreate) -> Conversation:
        return await self._repo.create_conversation(payload)

    async def get(self, conversation_id: str) -> Conversation:
        conversation = await self._repo.get_conversation(conversation_id)
        if conversation is None:
            raise not_found("conversation", conversation_id)
        return conversation

    async def get_optional(self, conversation_id: str) -> Conversation | None:
        return await self._repo.get_conversation(conversation_id)

    async def update(self, conversation_id: str, payload: ConversationUpdate) -> Conversation:
        conversation = await self._repo.update_conversation(conversation_id, payload)
        if conversation is None:
            raise not_found("conversation", conversation_id)
        return conversation

    async def delete(self, conversation_id: str) -> None:
        deleted = await self._repo.delete_conversation(conversation_id)
        if not deleted:
            raise not_found("conversation", conversation_id)

    async def list_for_actor(
        self, *, user_id: str, tenant_id: str, workspace_id: str | None = None
    ) -> list[Conversation]:
        return await self._repo.list_conversations(
            user_id=user_id, tenant_id=tenant_id, workspace_id=workspace_id
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
        approval = await self._repo.get_approval(approval_id)
        if approval is None:
            raise not_found("approval", approval_id)
        return approval

    async def resolve_approval(
        self,
        approval_id: str,
        *,
        status: str,
        user_id: str,
        tenant_id: str,
        run_generation: int | None = None,
    ) -> ApprovalRecord:
        approval = await self.get_approval(approval_id)
        self.ensure_ownership(
            Conversation(
                id=approval.conversation_id,
                user_id=approval.user_id,
                tenant_id=approval.tenant_id,
                title="",
            ),
            user_id=user_id,
            tenant_id=tenant_id,
        )
        if approval.status != "pending":
            raise conflict(f"Approval {approval_id!r} is already {approval.status}")
        if run_generation is not None and approval.run_generation != run_generation:
            raise conflict(f"Approval {approval_id!r} belongs to a superseded run generation")
        from tianzhou_agent_platform.core.base import utc_now

        return await self._repo.update_approval(
            approval_id,
            status=status,
            resolved_at=utc_now(),
        )
