"""Application workflow of a chat turn, its approval continuation, and stopping and resuming it.

ChatService resolves the actor's conversation, holds the conversation's run lease, opens and closes the run's
trace, selects the actor's model and delegates execution to the native ``AgentRunner``. It translates failures and
cancellation into the conversation's run status.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from tianzhou_agent_platform.conversations.models import Conversation, ConversationCreate
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord, ChatRequest, ChatResponse
from tianzhou_agent_platform.conversations.service import ConversationService
from tianzhou_agent_platform.core.errors import PlatformError, conflict
from tianzhou_agent_platform.model_providers.models import use_model_runtime
from tianzhou_agent_platform.model_providers.service import ModelProviderService
from tianzhou_agent_platform.services.agent_integration.runner import AgentRunner
from tianzhou_agent_platform.services.run_stops import RunStops

logger = logging.getLogger(__name__)

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


class ChatService:
    """Route-facing coordinator for one agent turn or approval continuation."""

    def __init__(
        self,
        *,
        conversations: ConversationService,
        model_providers: ModelProviderService,
        runner: AgentRunner,
        events: Any,
        stops: RunStops | None = None,
    ) -> None:
        self.conversations = conversations
        self.model_providers = model_providers
        self.runner = runner
        self.events = events
        self.stops = stops or RunStops()

    async def run_turn(
        self,
        request: ChatRequest,
        *,
        event_sink: EventSink | None = None,
        trace_id: str | None = None,
    ) -> ChatResponse:
        conversation = await self._conversation(request)
        trace_id = trace_id or f"trace_{uuid4().hex}"
        await self.events.start(
            trace_id=trace_id,
            conversation_id=conversation.id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            input_data={
                "message": request.message,
                "requested_capability": request.capability,
                "preferred_aina_id": request.preferred_aina_id,
            },
        )
        try:
            await self.conversations.start_run(conversation.id, trace_id)
            # New input supersedes a paused approval: its interrupt is abandoned and its calls are closed.
            for approval in await self.conversations.cancel_pending_approvals(conversation.id):
                await self.events.push(
                    approval.trace_id,
                    kind="approval.cancelled",
                    status="completed",
                    conversation_id=conversation.id,
                    details={"approval_id": approval.id},
                )
                await self.events.finish(approval.trace_id, "completed")
            conversation = await self.conversations.get(conversation.id)
            async with self.stops.watch(conversation.id, trace_id) as stop:
                with use_model_runtime(await self._model_runtime(request.user_id, request.tenant_id)):
                    response = await self.runner.run_turn(
                        conversation, request, trace_id=trace_id, event_sink=event_sink, stop=stop
                    )
        except BaseException as exc:
            await self._fail_run(conversation.id, trace_id, exc)
            raise
        await self._finish(conversation.id, trace_id, response)
        return response

    async def stop(self, conversation_id: str, *, user_id: str, tenant_id: str) -> None:
        """Ask the conversation's running turn to stop; the turn itself ends as ``stopped``."""
        conversation = await self.conversations.require_for_actor(
            conversation_id, user_id=user_id, tenant_id=tenant_id
        )
        if conversation.run_status != "running" or conversation.active_trace_id is None:
            raise conflict("This conversation has no running request")
        await self.stops.request(conversation.id, conversation.active_trace_id)

    async def resume(
        self,
        conversation_id: str,
        *,
        user_id: str,
        tenant_id: str,
        event_sink: EventSink | None = None,
    ) -> ChatResponse:
        """Continue a stopped turn from its checkpoint, as a new run of the conversation."""
        conversation = await self.conversations.require_for_actor(
            conversation_id, user_id=user_id, tenant_id=tenant_id
        )
        if conversation.run_status != "stopped":
            raise conflict("This conversation has no stopped request to continue")
        trace_id = f"trace_{uuid4().hex}"
        await self.events.start(
            trace_id=trace_id,
            conversation_id=conversation.id,
            user_id=user_id,
            tenant_id=tenant_id,
            input_data={"resume": True},
        )
        try:
            conversation = await self.conversations.start_run(conversation.id, trace_id)
            async with self.stops.watch(conversation.id, trace_id) as stop:
                with use_model_runtime(await self._model_runtime(user_id, tenant_id)):
                    response = await self.runner.resume_turn(
                        conversation, trace_id=trace_id, event_sink=event_sink, stop=stop
                    )
        except BaseException as exc:
            await self._fail_run(conversation.id, trace_id, exc)
            raise
        await self._finish(conversation.id, trace_id, response)
        return response

    async def confirm(self, approval_id: str, *, user_id: str, tenant_id: str) -> ChatResponse:
        approval, conversation = await self._pending_approval(approval_id, user_id=user_id, tenant_id=tenant_id)
        await self.conversations.start_run(conversation.id, approval.trace_id)
        try:
            with use_model_runtime(await self._model_runtime(user_id, tenant_id)):
                # Fails before the approval is resolved when the paused batch can no longer be resumed.
                continuation = await self.runner.open_approval(conversation, approval, approve=True)
        except BaseException:
            # The approval stays pending (it can still be denied) and the conversation stays paused.
            await self.conversations.finish_run(
                conversation.id,
                status="approval_required",
                expected_trace_id=approval.trace_id,
            )
            raise
        await self.conversations.set_approval_status(approval_id, "approved")
        await self.events.push(
            approval.trace_id,
            kind="approval.confirmed",
            status="completed",
            conversation_id=approval.conversation_id,
            details={"approval_id": approval_id},
        )
        # The resumed tool calls and model requests are recorded as children of the run's root span.
        await self.events.resume(approval.trace_id, conversation_id=conversation.id)
        try:
            async with self.stops.watch(conversation.id, approval.trace_id) as stop:
                with use_model_runtime(await self._model_runtime(user_id, tenant_id)):
                    response = await continuation.run(stop)
        except BaseException as exc:
            await self._fail_run(conversation.id, approval.trace_id, exc)
            raise
        await self.conversations.set_approval_status(approval_id, "executed")
        await self._finish(conversation.id, approval.trace_id, response)
        return response

    async def deny(self, approval_id: str, *, user_id: str, tenant_id: str) -> ApprovalRecord:
        approval, conversation = await self._pending_approval(approval_id, user_id=user_id, tenant_id=tenant_id)
        await self.conversations.start_run(conversation.id, approval.trace_id)
        try:
            with use_model_runtime(await self._model_runtime(user_id, tenant_id)):
                continuation = await self.runner.open_approval(conversation, approval, approve=False)
                denied = await self.conversations.set_approval_status(approval_id, "denied")
                await self.events.push(
                    approval.trace_id,
                    kind="approval.denied",
                    status="completed",
                    conversation_id=approval.conversation_id,
                    details={"approval_id": approval_id},
                )
                # Closes the paused batch without running it or asking the model for another answer.
                await continuation.run()
        except BaseException as exc:
            await self._fail_run(conversation.id, approval.trace_id, exc)
            raise
        await self.conversations.finish_run(conversation.id, expected_trace_id=approval.trace_id)
        await self.events.finish(approval.trace_id, "completed")
        return denied

    async def delete_conversation(self, conversation_id: str) -> None:
        """Delete the conversation together with its working memory."""
        await self.conversations.mark_deleted(conversation_id)
        await self.runner.delete_working_memory(conversation_id)

    async def _conversation(self, request: ChatRequest) -> Conversation:
        if request.conversation_id is None:
            return await self.conversations.create(
                ConversationCreate(
                    user_id=request.user_id,
                    tenant_id=request.tenant_id,
                    workspace_id=request.workspace_id,
                )
            )
        conversation = await self.conversations.require_for_actor(
            request.conversation_id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
        )
        if request.workspace_id is not None and request.workspace_id != conversation.workspace_id:
            raise conflict("Requested workspace does not match the conversation")
        return conversation

    async def _pending_approval(
        self,
        approval_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> tuple[ApprovalRecord, Conversation]:
        approval = await self.conversations.get_approval(approval_id)
        if approval.user_id != user_id or approval.tenant_id != tenant_id:
            raise PlatformError("PERMISSION_DENIED", "Approval ownership does not match the caller", status_code=403)
        if approval.status != "pending":
            raise PlatformError("CONFLICT", f"Approval is already {approval.status}", status_code=409)
        conversation = await self.conversations.require_for_actor(
            approval.conversation_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        return approval, conversation

    async def _model_runtime(self, user_id: str, tenant_id: str) -> Any:
        return await self.model_providers.default_runtime(user_id=user_id, tenant_id=tenant_id)

    async def _finish(self, conversation_id: str, trace_id: str, response: ChatResponse) -> None:
        await self.conversations.finish_run(
            conversation_id,
            status="idle" if response.status == "completed" else response.status,
            error=response.content if response.status == "failed" else None,
            expected_trace_id=trace_id,
        )
        root_span_id = self.events.root_span_id(trace_id)
        # A stopped run ends its trace; resuming it is a new run.
        trace_status = "cancelled" if response.status == "stopped" else response.status
        # A paused run keeps its root span open for the resume.
        if response.status != "approval_required" and root_span_id is not None:
            await self.events.finish_span(
                trace_id,
                root_span_id,
                trace_status if trace_status in {"completed", "cancelled"} else "failed",
                output_data={
                    "content": response.content,
                    "status": response.status,
                    "message_id": response.message_id,
                    "widgets": [widget.model_dump(mode="json") for widget in response.widgets],
                },
                attributes={
                    "iterations": response.iterations,
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "usage_estimated": response.usage.estimated,
                },
            )
        await self.events.finish(trace_id, trace_status)

    async def _fail_run(self, conversation_id: str, trace_id: str, exc: BaseException) -> None:
        if isinstance(exc, asyncio.CancelledError):
            error = "The agent run was interrupted. Please retry the request."
        elif isinstance(exc, PlatformError):
            error = exc.user_message or exc.message
        else:
            logger.exception("Agent run failed unexpectedly", extra={"trace_id": trace_id})
            error = "The agent run failed unexpectedly."
        try:
            conversation = await self.conversations.get(conversation_id)
            if conversation.run_status == "running" and conversation.active_trace_id == trace_id:
                await self.conversations.finish_run(
                    conversation_id,
                    status="failed",
                    error=error,
                    expected_trace_id=trace_id,
                )
        except Exception:
            logger.exception(
                "Failed to release conversation run",
                extra={"conversation_id": conversation_id, "trace_id": trace_id},
            )
        # Close the run's trace as _finish does; otherwise it stays "running".
        root_span_id = self.events.root_span_id(trace_id)
        if root_span_id is not None:
            await self.events.finish_span(
                trace_id, root_span_id, "failed", error={"type": type(exc).__name__, "message": error}
            )
        await self.events.finish(trace_id, "failed")
