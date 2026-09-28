"""Application workflow: chat turn / approval continuation coordination.

ChatService resolves actor-owned conversation state, obtains a run lease,
prepares execution, invokes/resumes the agent, reconciles results, finishes
the run and returns application response schemas.

While the native runtime migration is incomplete, `run_turn`/`confirm`/`deny`
delegate to the legacy AgentRuntime through an explicit temporary branch.
"""

from __future__ import annotations

from typing import Any, Protocol
from uuid import uuid4

from tianzhou_agent_platform.conversations.models import Conversation, Message
from tianzhou_agent_platform.conversations.schemas import (
    ApprovalAction,
    ApprovalRecord,
    ChatRequest,
    ChatResponse,
)
from tianzhou_agent_platform.conversations.service import ConversationService
from tianzhou_agent_platform.core.base import Usage, utc_now


class LegacyAgentPort(Protocol):
    """Temporary port to the legacy AgentRuntime during migration."""

    async def run(self, request: ChatRequest) -> ChatResponse: ...

    async def confirm(self, conversation_id: str, approval_id: str, action: ApprovalAction) -> ChatResponse: ...

    async def deny(self, conversation_id: str, approval_id: str, action: ApprovalAction) -> ChatResponse: ...


class ChatService:
    """Route-facing application coordinator for one agent turn or approval continuation."""

    def __init__(
        self,
        conversations: ConversationService,
        *,
        legacy_agent: LegacyAgentPort | None = None,
        native_runner: Any | None = None,
    ) -> None:
        self._conversations = conversations
        self._legacy_agent = legacy_agent
        self._native_runner = native_runner

    async def run_turn(
        self, request: ChatRequest, *, event_sink: Any | None = None, trace_id: str | None = None
    ) -> ChatResponse:
        conversation = await self._ensure_conversation(request)
        self._conversations.ensure_ownership(
            conversation, user_id=request.user_id, tenant_id=request.tenant_id
        )
        # No TypeError fallbacks below: retrying with another signature would re-run a turn whose tools may
        # already have executed side effects.
        if self._native_runner is not None:
            # New user input supersedes any pending approval before accepting work. The AgentRuntime path does
            # this itself, recording approval.cancelled and closing the paused run's trace.
            await self._invalidate_pending_approvals(conversation.id)
            return await self._native_runner.run_turn(request, conversation, event_sink=event_sink)
        if self._legacy_agent is not None:
            # Accept both ChatService protocol (run) and AgentRuntime (chat).
            run = getattr(self._legacy_agent, "run", None) or getattr(self._legacy_agent, "chat", None)
            if run is None:
                raise RuntimeError("ChatService has no execution backend configured")
            return await run(request, event_sink=event_sink, trace_id=trace_id)
        raise RuntimeError("ChatService has no execution backend configured")

    async def confirm(
        self, conversation_id: str, approval_id: str, action: ApprovalAction
    ) -> ChatResponse:
        approval = await self._conversations.get_approval(approval_id)
        self._conversations.ensure_ownership(
            Conversation(
                id=approval.conversation_id,
                user_id=approval.user_id,
                tenant_id=approval.tenant_id,
                title="",
            ),
            user_id=action.user_id,
            tenant_id=action.tenant_id,
        )
        if self._native_runner is not None:
            return await self._native_runner.confirm(conversation_id, approval_id, action)
        if self._legacy_agent is not None:
            return await self._legacy_agent.confirm(conversation_id, approval_id, action)
        raise RuntimeError("ChatService has no execution backend configured")

    async def deny(
        self, conversation_id: str, approval_id: str, action: ApprovalAction
    ) -> ChatResponse:
        approval = await self._conversations.get_approval(approval_id)
        self._conversations.ensure_ownership(
            Conversation(
                id=approval.conversation_id,
                user_id=approval.user_id,
                tenant_id=approval.tenant_id,
                title="",
            ),
            user_id=action.user_id,
            tenant_id=action.tenant_id,
        )
        if self._native_runner is not None:
            return await self._native_runner.deny(conversation_id, approval_id, action)
        if self._legacy_agent is not None:
            return await self._legacy_agent.deny(conversation_id, approval_id, action)
        raise RuntimeError("ChatService has no execution backend configured")

    async def _ensure_conversation(self, request: ChatRequest) -> Conversation:
        if request.conversation_id:
            return await self._conversations.get(request.conversation_id)
        from tianzhou_agent_platform.conversations.models import ConversationCreate

        return await self._conversations.create(
            ConversationCreate(
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                workspace_id=request.workspace_id,
            )
        )

    async def _invalidate_pending_approvals(self, conversation_id: str) -> None:
        """A new user turn must invalidate pending approvals and cancel paused work."""
        try:
            approvals = await self._conversations._repo.list_approvals(  # noqa: SLF001
                conversation_id=conversation_id, status="pending"
            )
        except Exception:
            return
        for approval in approvals:
            try:
                await self._conversations._repo.update_approval(  # noqa: SLF001
                    approval.id, status="denied", resolved_at=utc_now()
                )
            except Exception:
                continue
