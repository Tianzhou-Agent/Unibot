"""Native conversation runner: one agent turn or approval continuation.

Application workflow implementation used by ChatService when
``native_agent_enabled`` is on. Coordinates conversation archive, native
agent invocation/resume, HITL mapping and public response schemas.

Thread identity: ``lc-v2:<conversation_id>`` (stable, server-derived).
Trace IDs stay separate. Only new input is submitted after initialization.
"""

from __future__ import annotations

from typing import Any, Callable, Sequence
from uuid import uuid4

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.types import Command

from tianzhou_agent_platform.conversations.models import Conversation
from tianzhou_agent_platform.conversations.schemas import (
    ApprovalAction,
    ApprovalRecord,
    ChatRequest,
    ChatResponse,
)
from tianzhou_agent_platform.conversations.service import ConversationService
from tianzhou_agent_platform.core.base import Usage
from tianzhou_agent_platform.core.errors import conflict
from tianzhou_agent_platform.services.agent_integration.approvals import (
    approve_all,
    approval_record_from_interrupt,
    build_hitl_middleware,
    reject_all,
)
from tianzhou_agent_platform.services.agent_integration.builder import build_chat_agent
from tianzhou_agent_platform.services.agent_integration.history import (
    native_to_archive,
    new_turn_inputs,
)

THREAD_PREFIX = "lc-v2:"


def thread_id_for(conversation_id: str) -> str:
    return f"{THREAD_PREFIX}{conversation_id}"


class NativeAgentRunner:
    """ChatService native execution backend (plan §5.2 / §6)."""

    def __init__(
        self,
        *,
        model: BaseChatModel | str,
        conversations: ConversationService,
        checkpointer: Any | None = None,
        tools_provider: Callable[[ChatRequest], Sequence[BaseTool]] | None = None,
        risky_tools: dict[str, Any] | None = None,
        summarization_model: BaseChatModel | str | None = None,
        observation_sink: Any | None = None,
        model_run_limit: int | None = 8,
        system_prompt: str | None = None,
    ) -> None:
        self._model = model
        self._conversations = conversations
        self._checkpointer = checkpointer
        self._tools_provider = tools_provider
        self._risky_tools = risky_tools or {}
        self._summarization_model = summarization_model
        self._observation_sink = observation_sink
        self._model_run_limit = model_run_limit
        self._system_prompt = system_prompt or "You are a helpful assistant."

    def _build_agent(self, request: ChatRequest) -> Any:
        tools = list(self._tools_provider(request)) if self._tools_provider else []
        middleware: list[Any] = []
        if self._risky_tools:
            middleware.append(build_hitl_middleware(risky_tools=self._risky_tools))
        return build_chat_agent(
            model=self._model,
            tools=tools,
            system_prompt=self._system_prompt,
            checkpointer=self._checkpointer,
            middleware=middleware,
            observation_sink=self._observation_sink,
            model_run_limit=self._model_run_limit,
            summarization_model=self._summarization_model,
        )

    async def run_turn(
        self,
        request: ChatRequest,
        conversation: Conversation,
        *,
        event_sink: Any | None = None,
    ) -> ChatResponse:
        trace_id = f"trace_{uuid4().hex}"
        agent = self._build_agent(request)
        config = {"configurable": {"thread_id": thread_id_for(conversation.id)}}

        # Archive the user turn first (originals survive compaction).
        user_input = new_turn_inputs(request.message)
        await self._archive(conversation.id, user_input, trace_id=trace_id)

        state_input: dict[str, Any] = {"messages": user_input}
        result = await agent.ainvoke(state_input, config=config)
        return await self._reconcile(
            conversation,
            result,
            trace_id=trace_id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            event_sink=event_sink,
        )

    async def confirm(
        self, conversation_id: str, approval_id: str, action: ApprovalAction
    ) -> ChatResponse:
        approval = await self._conversations.get_approval(approval_id)
        if approval.status != "pending":
            raise conflict(f"Approval {approval_id!r} is already {approval.status}")
        return await self._resume(conversation_id, approval, decisions=approve_all(len(approval.tool_calls) or 1))

    async def deny(
        self, conversation_id: str, approval_id: str, action: ApprovalAction
    ) -> ChatResponse:
        approval = await self._conversations.get_approval(approval_id)
        if approval.status != "pending":
            raise conflict(f"Approval {approval_id!r} is already {approval.status}")
        # Denial closes pending calls without another model generation.
        return await self._resume(
            conversation_id, approval, decisions=reject_all(len(approval.tool_calls) or 1)
        )

    async def _resume(
        self, conversation_id: str, approval: ApprovalRecord, *, decisions: dict[str, Any]
    ) -> ChatResponse:
        # Resume does not submit new user input; only continue the checkpointed graph.
        agent = self._build_agent_for_resume(conversation_id, approval)
        config = {"configurable": {"thread_id": thread_id_for(conversation_id)}}
        result = await agent.ainvoke(Command(resume=decisions), config=config)
        await self._conversations.resolve_approval(
            approval.id,
            status="approved" if decisions["decisions"][0].get("type") == "approve" else "denied",
            user_id=approval.user_id,
            tenant_id=approval.tenant_id,
        )
        return await self._reconcile(None, result, trace_id=approval.trace_id, conversation_id=conversation_id)

    def _build_agent_for_resume(self, conversation_id: str, approval: ApprovalRecord) -> Any:
        tools = (
            list(self._tools_provider(ChatRequest(message="resume", conversation_id=conversation_id)))
            if self._tools_provider
            else []
        )
        middleware: list[Any] = []
        if self._risky_tools:
            middleware.append(build_hitl_middleware(risky_tools=self._risky_tools))
        return build_chat_agent(
            model=self._model,
            tools=tools,
            system_prompt=self._system_prompt,
            checkpointer=self._checkpointer,
            middleware=middleware,
            observation_sink=self._observation_sink,
            model_run_limit=self._model_run_limit,
            summarization_model=self._summarization_model,
        )

    async def _reconcile(
        self,
        conversation: Conversation | None,
        result: dict[str, Any],
        *,
        trace_id: str,
        conversation_id: str | None = None,
        user_id: str = "",
        tenant_id: str = "",
        event_sink: Any | None = None,
    ) -> ChatResponse:
        cid = conversation_id or (conversation.id if conversation else "")
        messages = list(result.get("messages") or [])
        await self._archive_native(cid, messages, trace_id=trace_id)

        final_text = ""
        last_ai = None
        for message in reversed(messages):
            if isinstance(message, AIMessage) and not message.tool_calls:
                last_ai = message
                final_text = message.content if isinstance(message.content, str) else str(message.content or "")
                break

        status: str = "completed"
        approval: ApprovalRecord | None = None
        if result.get("__interrupt__") or self._has_pending_risky(messages):
            status = "approval_required"
            approval = await self._persist_interrupt(
                cid,
                messages,
                trace_id=trace_id,
                result=result,
                user_id=user_id,
                tenant_id=tenant_id,
            )
        elif "Incomplete response" in final_text or "empty response" in final_text.lower():
            status = "failed"

        usage = Usage()
        if last_ai is not None and last_ai.usage_metadata:
            usage = Usage(
                input_tokens=last_ai.usage_metadata.get("input_tokens", 0),
                output_tokens=last_ai.usage_metadata.get("output_tokens", 0),
            )

        return ChatResponse(
            conversation_id=cid,
            message_id=messages[-1].id if messages else None,
            content=final_text,
            status=status,  # type: ignore[arg-type]
            trace_id=trace_id,
            iterations=1,
            usage=usage,
            approval=approval,
        )

    def _has_pending_risky(self, messages: list[Any]) -> bool:
        if not self._risky_tools:
            return False
        for message in reversed(messages):
            if isinstance(message, AIMessage) and message.tool_calls:
                names = {c.get("name") for c in message.tool_calls}
                if names & set(self._risky_tools):
                    # No ToolMessage yet for those calls → paused.
                    executed = {m.tool_call_id for m in messages if isinstance(m, ToolMessage)}
                    pending = {c.get("id") for c in message.tool_calls if c.get("name") in self._risky_tools}
                    return bool(pending - executed)
        return False

    async def _persist_interrupt(
        self,
        conversation_id: str,
        messages: list[Any],
        *,
        trace_id: str,
        result: dict[str, Any],
        user_id: str = "",
        tenant_id: str = "",
    ) -> ApprovalRecord | None:
        tool_calls: list[dict[str, Any]] = []
        for message in reversed(messages):
            if isinstance(message, AIMessage) and message.tool_calls:
                tool_calls = list(message.tool_calls)
                break
        risky = [c for c in tool_calls if c.get("name") in self._risky_tools]
        if not risky:
            return None
        record = approval_record_from_interrupt(
            conversation_id=conversation_id,
            user_id=user_id or "",
            tenant_id=tenant_id or "",
            trace_id=trace_id,
            tool_calls=tool_calls,
            capability_names=[str(c.get("name")) for c in risky],
            runtime_ref={
                "thread_id": thread_id_for(conversation_id),
                "engine": "native",
                "pending_call_ids": [c.get("id") for c in risky],
            },
        )
        return await self._conversations.create_approval(record)

    async def _archive(
        self, conversation_id: str, messages: list[Any], *, trace_id: str
    ) -> None:
        records = [native_to_archive(m, trace_id=trace_id) for m in messages]
        await self._conversations.append_messages_idempotent(conversation_id, records)

    async def _archive_native(
        self, conversation_id: str, messages: list[Any], *, trace_id: str
    ) -> None:
        """Archive original outputs (incl. tool results) with stable IDs."""
        records = [native_to_archive(m, trace_id=trace_id) for m in messages]
        # Archive writes are correctness-critical: fail explicitly (A8).
        await self._conversations.append_messages_idempotent(conversation_id, records)
