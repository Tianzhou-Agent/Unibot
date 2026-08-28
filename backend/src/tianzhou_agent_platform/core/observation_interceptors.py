"""Observability adapters applied outside the business agent runtime."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from contextvars import ContextVar
from dataclasses import dataclass
from time import perf_counter
from typing import Any, cast
from uuid import uuid4

from langgraph.checkpoint.base import BaseCheckpointSaver

from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.aina.document.task_service import DocumentEditTaskService
from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.aina.memory.models import MemoryRecord
from tianzhou_agent_platform.aina.protocol.models import (
    AinaCapability,
    AinaInstallation,
    AinaRecord,
)
from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.agent import (
    AgentRuntime,
    AgentState,
    Capability,
    ResolvedCapabilityOutcome,
)
from tianzhou_agent_platform.core.chat import ApprovalRecord, ChatRequest, ChatResponse
from tianzhou_agent_platform.core.context_compression import (
    COMPRESSION_CONFIG_KEY,
    active_history,
    estimate_request_tokens,
    plan_compression,
)
from tianzhou_agent_platform.core.conversation import Conversation, ConversationCreate, Message
from tianzhou_agent_platform.core.errors import PlatformError, conflict
from tianzhou_agent_platform.core.llm import EventSink, LLMClient, LLMResult
from tianzhou_agent_platform.core.model_settings import current_model_runtime
from tianzhou_agent_platform.core.observation_context import (
    ObservationContext,
    bind_observation_context,
    current_observation_context,
)
from tianzhou_agent_platform.core.observability import ObservabilityAspect
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.sandbox.service import SandboxService
from tianzhou_agent_platform.tasks.service import TaskService


@dataclass(slots=True)
class _RunCapture:
    requested_capability: str | None
    user_request_recorded: bool = False


_run_capture: ContextVar[_RunCapture | None] = ContextVar("agent_run_capture", default=None)
_model_iteration: ContextVar[int] = ContextVar("agent_model_iteration", default=0)
_model_capabilities: ContextVar[dict[str, Capability]] = ContextVar(
    "agent_model_capabilities",
    default={},
)
_recorded_capability_failures: ContextVar[frozenset[str]] = ContextVar(
    "agent_recorded_capability_failures",
    default=frozenset(),
)


class ObservedLLMClient:
    """Collect model spans and semantic events at the LLM port boundary."""

    def __init__(self, delegate: LLMClient, observability: ObservabilityAspect) -> None:
        self._delegate = delegate
        self._observability = observability

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None = None,
        event_sink: EventSink | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        context_type: str | None = None,
        context_id: str | None = None,
    ) -> LLMResult:
        context = current_observation_context()
        if (
            context is None
            or trace_id is None
            or trace_id != context.legacy_trace_id
            or context.root_span_id is None
        ):
            return await self._delegate.complete(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                event_sink=event_sink,
                trace_id=trace_id,
                span_id=span_id,
                context_type=context_type,
                context_id=context_id,
            )

        observed_span_id = span_id or f"span_{uuid4().hex}"
        runtime_model = current_model_runtime()
        delegate_settings = getattr(self._delegate, "settings", None)
        target_id = (
            runtime_model.model
            if runtime_model is not None
            else getattr(delegate_settings, "llm_model", None)
        )
        conversation_call = context_type == "conversation"
        iteration = _model_iteration.get()
        capabilities = _model_capabilities.get()
        if conversation_call:
            iteration += 1
            _model_iteration.set(iteration)
            await self._observability.record_event(
                trace_id,
                kind="model.requested",
                status="started",
                target_type="model",
                target_id=target_id,
                details={
                    "iteration": iteration,
                    "message_count": len(messages),
                    "message_roles": [str(message.get("role") or "unknown") for message in messages],
                    "capability_ids": sorted(
                        {capability.capability_id for capability in capabilities.values()}
                    ),
                    "forced_function": _forced_function(tool_choice),
                    "streaming": event_sink is not None,
                },
            )

        started = perf_counter()
        await self._observability.start_span(
            trace_id,
            span_id=observed_span_id,
            parent_span_id=context.root_span_id,
            kind="model",
            name="model.complete",
            target_id=target_id,
            input_data={
                "messages": [_model_message_preview(message) for message in messages],
                "tools": tools,
                "tool_choice": tool_choice,
            },
            attributes={
                "streaming": event_sink is not None,
                "context_type": context_type,
            },
        )
        try:
            result = await self._delegate.complete(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                event_sink=event_sink,
                trace_id=trace_id,
                span_id=observed_span_id,
                context_type=context_type,
                context_id=context_id,
            )
        except Exception as exc:
            error = _error_details(exc)
            await self._observability.finish_span(
                trace_id,
                observed_span_id,
                "failed",
                error=error,
            )
            if conversation_call:
                await self._observability.record_event(
                    trace_id,
                    kind="model.failed",
                    status="failed",
                    target_type="model",
                    target_id=target_id,
                    duration_ms=(perf_counter() - started) * 1000,
                    details={"iteration": iteration, **error},
                )
            raise

        await self._observability.finish_span(
            trace_id,
            observed_span_id,
            "completed",
            output_data=_model_message_preview(result.message),
            attributes={
                "finish_reason": result.finish_reason,
                "tool_call_count": len(result.message.get("tool_calls") or []),
                "content_length": len(str(result.message.get("content") or "")),
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "usage_estimated": result.usage_estimated,
                "ttft_ms": result.ttft_ms,
            },
            first_output_at=result.first_token_at,
        )
        if conversation_call:
            await self._observability.record_event(
                trace_id,
                kind="model.completed",
                status="completed",
                target_type="model",
                target_id=target_id,
                duration_ms=(perf_counter() - started) * 1000,
                details={
                    "iteration": iteration,
                    "finish_reason": result.finish_reason,
                    "tool_call_count": len(result.message.get("tool_calls") or []),
                    "tool_calls": [
                        _tool_call_details(call, capabilities)
                        for call in result.message.get("tool_calls") or []
                        if isinstance(call, dict)
                    ],
                    "content_length": len(str(result.message.get("content") or "")),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "usage_estimated": result.usage_estimated,
                },
            )
        return result

    async def aclose(self) -> None:
        close = getattr(self._delegate, "aclose", None)
        if close is not None:
            await close()


class ObservedAgentRepository:
    """Observe durable business state transitions made through the repository port."""

    def __init__(self, delegate: InMemoryRepository, observability: ObservabilityAspect) -> None:
        self._delegate = delegate
        self._observability = observability

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    async def append_provider_messages(
        self,
        conversation_id: str,
        messages: Iterable[dict[str, Any]],
        *,
        trace_id: str,
    ) -> list[Message]:
        materialized = list(messages)
        appended = await self._delegate.append_provider_messages(
            conversation_id,
            materialized,
            trace_id=trace_id,
        )
        capture = _run_capture.get()
        context = current_observation_context()
        if (
            capture is not None
            and not capture.user_request_recorded
            and context is not None
            and context.legacy_trace_id == trace_id
        ):
            for raw, saved in zip(materialized, appended, strict=True):
                if raw.get("role") != "user":
                    continue
                content = str(raw.get("content") or "")
                capture.user_request_recorded = True
                await self._observability.record_event(
                    trace_id,
                    kind="user.request",
                    status="completed",
                    details={
                        "message_id": saved.id,
                        "content": content,
                        "content_length": len(content),
                        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                        "requested_capability": capture.requested_capability,
                    },
                )
                break
        return appended

    async def close_dangling_tool_calls(
        self,
        conversation_id: str,
        *,
        trace_id: str,
    ) -> list[ApprovalRecord]:
        approvals = await self._delegate.close_dangling_tool_calls(
            conversation_id,
            trace_id=trace_id,
        )
        await self._observability.record_cancelled_approvals(approvals)
        return approvals

    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        created = await self._delegate.create_approval(approval)
        await self._observability.record_event(
            approval.trace_id,
            kind="approval.required",
            status="pending",
            target_type="capability",
            details={
                "approval_id": approval.id,
                "capabilities": approval.capability_names,
                "calls": [
                    _tool_call_details(call, _model_capabilities.get())
                    for call in approval.tool_calls
                ],
            },
        )
        return created

    async def set_approval_status(self, approval_id: str, status: str) -> ApprovalRecord:
        approval = await self._delegate.set_approval_status(approval_id, status)
        kind = {"approved": "approval.confirmed", "denied": "approval.denied"}.get(status)
        if kind is not None:
            await self._observability.record_event(
                approval.trace_id,
                kind=kind,
                status="completed",
                conversation_id=approval.conversation_id,
                details={"approval_id": approval_id},
            )
        return approval


class ObservedAgentRuntime(AgentRuntime):
    """AOP-style runtime adapter; the inherited AgentRuntime remains observation-free."""

    def __init__(
        self,
        *,
        settings: AgentSettings,
        repository: InMemoryRepository,
        llm: LLMClient,
        gateway: RemoteCapabilityGateway,
        observability: ObservabilityAspect,
        document_service: DocumentService | None = None,
        document_edit_task_service: DocumentEditTaskService | None = None,
        sandbox_service: SandboxService | None = None,
        task_service: TaskService | None = None,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
    ) -> None:
        self._observability = observability
        observed_repository = cast(
            InMemoryRepository,
            ObservedAgentRepository(repository, observability),
        )
        super().__init__(
            settings=settings,
            repository=observed_repository,
            llm=ObservedLLMClient(llm, observability),
            gateway=gateway,
            document_service=document_service,
            document_edit_task_service=document_edit_task_service,
            sandbox_service=sandbox_service,
            task_service=task_service,
            checkpointer=checkpointer,
        )

    async def chat(
        self,
        request: ChatRequest,
        *,
        event_sink: EventSink | None = None,
        trace_id: str | None = None,
    ) -> ChatResponse:
        if request.conversation_id is None:
            conversation = await self.repository.create_conversation(
                ConversationCreate(
                    user_id=request.user_id,
                    tenant_id=request.tenant_id,
                    workspace_id=request.workspace_id,
                )
            )
            request = request.model_copy(update={"conversation_id": conversation.id})
        else:
            conversation = await self.repository.require_conversation_actor(
                request.conversation_id,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
            )
            if request.workspace_id is not None and request.workspace_id != conversation.workspace_id:
                raise conflict("Requested workspace does not match the conversation")

        run_trace_id = trace_id or f"trace_{uuid4().hex}"
        root_span_id = f"span_{uuid4().hex}"
        await self._observability.create_agent_trace(
            trace_id=run_trace_id,
            root_span_id=root_span_id,
            conversation_id=conversation.id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            input_data={
                "message": request.message,
                "requested_capability": request.capability,
                "preferred_aina_id": request.preferred_aina_id,
                **(
                    {"workspace_id": conversation.workspace_id}
                    if conversation.workspace_id is not None
                    else {}
                ),
            },
            attributes={
                "conversation_id": conversation.id,
                "requested_capability": request.capability,
                "preferred_aina_id": request.preferred_aina_id,
                **(
                    {"workspace_id": conversation.workspace_id}
                    if conversation.workspace_id is not None
                    else {}
                ),
            },
        )
        context = ObservationContext(
            legacy_trace_id=run_trace_id,
            conversation_id=conversation.id,
            user_id=request.user_id,
            tenant_id=request.tenant_id,
            root_span_id=root_span_id,
        )
        capture_token = _run_capture.set(_RunCapture(request.capability))
        iteration_token = _model_iteration.set(0)
        failure_token = _recorded_capability_failures.set(frozenset())
        try:
            with bind_observation_context(context):
                response = await super().chat(
                    request,
                    event_sink=event_sink,
                    trace_id=run_trace_id,
                )
                await self._finish_response(response, root_span_id)
                return response
        except Exception:
            await self._observability.finish_trace(run_trace_id, "failed")
            raise
        finally:
            _recorded_capability_failures.reset(failure_token)
            _model_iteration.reset(iteration_token)
            _run_capture.reset(capture_token)

    async def confirm(self, approval_id: str, *, user_id: str, tenant_id: str) -> ChatResponse:
        approval, conversation = await self._approval_context(
            approval_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        root_span_id = await self._observability.ensure_agent_root_span(
            approval.trace_id,
            span_id=f"span_{uuid4().hex}",
            conversation_id=conversation.id,
        )
        context = ObservationContext(
            legacy_trace_id=approval.trace_id,
            conversation_id=conversation.id,
            user_id=user_id,
            tenant_id=tenant_id,
            root_span_id=root_span_id,
        )
        iteration_token = _model_iteration.set(0)
        failure_token = _recorded_capability_failures.set(frozenset())
        try:
            with bind_observation_context(context):
                response = await super().confirm(
                    approval_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                )
                await self._finish_response(response, root_span_id)
                return response
        except Exception:
            await self._observability.finish_trace(approval.trace_id, "failed")
            raise
        finally:
            _recorded_capability_failures.reset(failure_token)
            _model_iteration.reset(iteration_token)

    async def deny(self, approval_id: str, *, user_id: str, tenant_id: str) -> ApprovalRecord:
        approval, conversation = await self._approval_context(
            approval_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        root_span_id = await self._observability.ensure_agent_root_span(
            approval.trace_id,
            span_id=f"span_{uuid4().hex}",
            conversation_id=conversation.id,
        )
        context = ObservationContext(
            legacy_trace_id=approval.trace_id,
            conversation_id=conversation.id,
            user_id=user_id,
            tenant_id=tenant_id,
            root_span_id=root_span_id,
        )
        with bind_observation_context(context):
            denied = await super().deny(
                approval_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            await self._observability.finish_trace(approval.trace_id, "completed")
            return denied

    async def _approval_context(
        self,
        approval_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> tuple[ApprovalRecord, Conversation]:
        approval = await self.repository.get_approval(approval_id)
        if approval.user_id != user_id or approval.tenant_id != tenant_id:
            raise PlatformError(
                "PERMISSION_DENIED",
                "Approval ownership does not match the caller",
                status_code=403,
            )
        if approval.status != "pending":
            raise PlatformError(
                "CONFLICT",
                f"Approval is already {approval.status}",
                status_code=409,
            )
        conversation = await self.repository.require_conversation_actor(
            approval.conversation_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        if not conversation.messages or not conversation.messages[-1].tool_calls:
            raise PlatformError(
                "CONFLICT",
                "The approval no longer has a pending tool call",
                status_code=409,
            )
        return approval, conversation

    async def _finish_response(self, response: ChatResponse, root_span_id: str) -> None:
        widget_refs = [{"id": widget.id, "kind": widget.kind} for widget in response.widgets]
        self._observability.add_trace_token_usage(
            response.trace_id,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
        await self._observability.record_event(
            response.trace_id,
            kind="final.response",
            status=response.status,
            details={
                "iterations": response.iterations,
                "message_id": response.message_id,
                "content": response.content,
                "content_length": len(response.content),
                "input_tokens": response.usage.input_tokens,
                "output_tokens": response.usage.output_tokens,
                "usage_estimated": response.usage.estimated,
                "widgets": widget_refs,
            },
        )
        if response.status != "approval_required":
            await self._observability.finish_span(
                response.trace_id,
                root_span_id,
                "completed" if response.status == "completed" else "failed",
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
        await self._observability.finish_trace(response.trace_id, response.status)

    async def _dispatch(
        self,
        *,
        conversation: Conversation,
        trace_id: str,
        requested_capability: str | None,
        requested_source: str | None,
        preferred_aina_id: str | None,
        ui_context: str | None,
        event_sink: EventSink | None,
    ) -> ChatResponse:
        all_capabilities = await self._available_capabilities(conversation)
        selected: Capability | None = None
        source = "unified_entry"
        if requested_capability is not None:
            forced_function = self._resolve_forced_capability(
                requested_capability,
                all_capabilities,
            )
            selected = all_capabilities.get(forced_function or "")
            source = requested_source or "explicit_capability"
        elif preferred_aina_id is not None:
            forced_function = self._resolve_forced_capability(
                f"aina:{preferred_aina_id}",
                all_capabilities,
            )
            selected = all_capabilities.get(forced_function or "")
            source = "preferred_aina"
        await self._record_scope_resolution(
            trace_id,
            conversation,
            selected=selected,
            source=source,
            requested_capability=requested_capability,
            preferred_aina_id=preferred_aina_id,
        )
        return await super()._dispatch(
            conversation=conversation,
            trace_id=trace_id,
            requested_capability=requested_capability,
            requested_source=requested_source,
            preferred_aina_id=preferred_aina_id,
            ui_context=ui_context,
            event_sink=event_sink,
        )

    async def _run(
        self,
        *,
        conversation: Conversation,
        trace_id: str,
        forced_capability: str | None = None,
        approved_call_ids: set[str] | None = None,
        resume: bool = False,
        event_sink: EventSink | None = None,
        capabilities: dict[str, Capability] | None = None,
        system_prompt: str | None = None,
        memory_context: list[MemoryRecord] | None = None,
    ) -> ChatResponse:
        resolved_capabilities = capabilities or await self._available_capabilities(conversation)
        forced_function = self._resolve_forced_capability(
            forced_capability,
            resolved_capabilities,
        )
        await self._observability.record_event(
            trace_id,
            kind="capability.discovery",
            status="completed",
            details={
                "aina_graph": await self._aina_graph(conversation, resolved_capabilities),
                "model_scope": _model_scope_details(
                    resolved_capabilities,
                    forced_capability=forced_capability,
                    forced_function=forced_function,
                ),
            },
        )
        capabilities_token = _model_capabilities.set(resolved_capabilities)
        try:
            return await super()._run(
                conversation=conversation,
                trace_id=trace_id,
                forced_capability=forced_capability,
                approved_call_ids=approved_call_ids,
                resume=resume,
                event_sink=event_sink,
                capabilities=resolved_capabilities,
                system_prompt=system_prompt,
                memory_context=memory_context,
            )
        finally:
            _model_capabilities.reset(capabilities_token)

    async def _invoke_resolved_capability(
        self,
        *,
        state: AgentState,
        event_sink: EventSink | None,
        capability: Capability,
        call_id: str,
        function_name: str,
        arguments: dict[str, Any],
        available_tool_ids: list[str],
        messages: list[dict[str, Any]],
        widgets: list[WidgetDefinition],
    ) -> ResolvedCapabilityOutcome:
        trace_id = state["trace_id"]
        span_id = f"span_{uuid4().hex}"
        context = current_observation_context()
        if context is None or context.root_span_id is None:
            return await super()._invoke_resolved_capability(
                state=state,
                event_sink=event_sink,
                capability=capability,
                call_id=call_id,
                function_name=function_name,
                arguments=arguments,
                available_tool_ids=available_tool_ids,
                messages=messages,
                widgets=widgets,
            )
        await self._observability.record_event(
            trace_id,
            kind=f"{capability.kind}.requested",
            status="started",
            target_type=capability.kind,
            target_id=capability.capability_id,
            details={
                "call_id": call_id,
                "function_name": function_name,
                "argument_fields": sorted(arguments),
                "arguments": arguments,
            },
        )
        await self._observability.start_span(
            trace_id,
            span_id=span_id,
            parent_span_id=context.root_span_id,
            kind="aina" if capability.kind == "aina" else "tool",
            name=function_name,
            target_id=capability.capability_id,
            target_version=_capability_version(capability),
            logical_call_id=call_id,
            input_data=arguments,
        )
        try:
            outcome = await super()._invoke_resolved_capability(
                state=state,
                event_sink=event_sink,
                capability=capability,
                call_id=call_id,
                function_name=function_name,
                arguments=arguments,
                available_tool_ids=available_tool_ids,
                messages=messages,
                widgets=widgets,
            )
        except Exception as exc:
            error = _error_details(exc)
            if not isinstance(exc, PlatformError) and isinstance(exc, (TypeError, ValueError)):
                dependency_failure = capability.kind == "aina"
                error = {
                    "code": "DEPENDENCY_FAILED" if dependency_failure else "INVALID_REQUEST",
                    "message": (
                        f"The AINA returned invalid data: {exc}"
                        if dependency_failure
                        else f"Capability arguments produced invalid data: {exc}"
                    ),
                    "retryable": False,
                }
            await self._observability.finish_span(
                trace_id,
                span_id,
                "failed",
                error=error,
            )
            await self._record_capability_failure(
                trace_id,
                capability=capability,
                call_id=call_id,
                function_name=function_name,
                code=str(error.get("code") or "DEPENDENCY_FAILED"),
                message=str(error.get("message") or str(exc)),
                retryable=bool(error.get("retryable", False)),
            )
            _recorded_capability_failures.set(
                _recorded_capability_failures.get() | {call_id}
            )
            raise
        await self._observability.finish_span(
            trace_id,
            span_id,
            "completed",
            input_data=arguments,
            output_data=outcome.result,
            attributes={
                "arguments": arguments,
                "result": outcome.result,
                "duration_ms": outcome.duration_ms,
                "result_size_bytes": outcome.result_size_bytes,
                "widgets": outcome.widgets,
            },
        )
        await self._observability.record_event(
            trace_id,
            kind=f"{capability.kind}.completed",
            status="completed",
            target_type=capability.kind,
            target_id=capability.capability_id,
            duration_ms=outcome.duration_ms,
            details={
                "call_id": call_id,
                "function_name": function_name,
                "result": outcome.result,
                "result_size_bytes": outcome.result_size_bytes,
                "widgets": outcome.widgets,
            },
        )
        return outcome

    async def _append_tool_error(
        self,
        state: AgentState,
        event_sink: EventSink | None,
        messages: list[dict[str, Any]],
        *,
        call_id: str,
        name: str,
        code: str,
        message: str,
        capability: Capability | None = None,
        recovery: dict[str, Any] | None = None,
    ) -> None:
        recorded = _recorded_capability_failures.get()
        if call_id in recorded:
            _recorded_capability_failures.set(recorded - {call_id})
        else:
            await self._record_capability_failure(
                state["trace_id"],
                capability=capability,
                call_id=call_id,
                function_name=name,
                code=code,
                message=message,
                retryable=code in {"TIMEOUT", "RATE_LIMITED"},
                recovery=recovery,
            )
        await super()._append_tool_error(
            state,
            event_sink,
            messages,
            call_id=call_id,
            name=name,
            code=code,
            message=message,
            capability=capability,
            recovery=recovery,
        )

    async def _record_capability_failure(
        self,
        trace_id: str,
        *,
        capability: Capability | None,
        call_id: str,
        function_name: str,
        code: str,
        message: str,
        retryable: bool,
        recovery: dict[str, Any] | None = None,
    ) -> None:
        kind = capability.kind if capability is not None else "tool"
        await self._observability.record_event(
            trace_id,
            kind=f"{kind}.failed",
            status="failed",
            target_type=kind if capability is not None else "capability",
            target_id=capability.capability_id if capability is not None else function_name,
            details={
                "call_id": call_id,
                "function_name": function_name,
                "code": code,
                "message": message,
                "retryable": retryable,
                "recovery": (
                    {
                        "owner_aina_id": recovery["owner_aina_id"],
                        "entry_function_name": recovery["entry_function_name"],
                    }
                    if recovery is not None
                    else None
                ),
            },
        )

    async def _activate_aina_model_scope(
        self,
        state: AgentState,
        *,
        event_sink: EventSink | None,
        capability: Capability,
        call_id: str,
        function_name: str,
        arguments: dict[str, Any],
        messages: list[dict[str, Any]],
    ) -> dict[str, Capability]:
        capabilities = await super()._activate_aina_model_scope(
            state,
            event_sink=event_sink,
            capability=capability,
            call_id=call_id,
            function_name=function_name,
            arguments=arguments,
            messages=messages,
        )
        _model_capabilities.set(capabilities)
        await self._observability.record_event(
            state["trace_id"],
            kind="routing.scope.activated",
            status="completed",
            target_type="aina",
            target_id=capability.capability_id,
            details={
                "call_id": call_id,
                "function_name": function_name,
                "arguments": arguments,
                "model_scope": _model_scope_details(
                    capabilities,
                    forced_capability=None,
                    forced_function=None,
                ),
            },
        )
        conversation = await self.repository.get_conversation(state["conversation_id"])
        await self._record_scope_resolution(
            state["trace_id"],
            conversation,
            selected=capability,
            source="model_selection",
            requested_capability=None,
            preferred_aina_id=None,
        )
        return capabilities

    async def _record_scope_resolution(
        self,
        trace_id: str,
        conversation: Conversation,
        *,
        selected: Capability | None,
        source: str,
        requested_capability: str | None,
        preferred_aina_id: str | None,
    ) -> None:
        await self._observability.record_event(
            trace_id,
            kind="routing.scope.resolved",
            status="completed",
            target_type=selected.kind if selected is not None else "system",
            target_id=selected.capability_id if selected is not None else None,
            details={
                "source": source,
                "requested_capability": requested_capability,
                "preferred_aina_id": preferred_aina_id,
                "active_aina_ids": conversation.active_aina_ids,
                "primary_aina_id": conversation.primary_aina_id,
                "last_aina_id": conversation.last_aina_id,
            },
        )

    async def _prepare_context(
        self,
        *,
        conversation: Conversation,
        trace_id: str,
        system_prompt: str,
        tool_definitions: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], int, int]:
        history = active_history(conversation)
        active_messages = [
            {"role": "system", "content": system_prompt},
            *history.provider_messages(),
        ]
        before_tokens = estimate_request_tokens(active_messages, tool_definitions)
        threshold_tokens = int(
            self.settings.context_window_tokens
            * self.settings.context_compression_threshold_ratio
        )
        plan = (
            plan_compression(
                history,
                keep_recent_turns=self.settings.context_compression_keep_recent_turns,
                min_messages=self.settings.context_compression_min_messages,
            )
            if self.settings.context_compression_enabled and before_tokens >= threshold_tokens
            else None
        )
        if plan is None:
            return await super()._prepare_context(
                conversation=conversation,
                trace_id=trace_id,
                system_prompt=system_prompt,
                tool_definitions=tool_definitions,
            )

        context = current_observation_context()
        if context is None or context.root_span_id is None:
            return await super()._prepare_context(
                conversation=conversation,
                trace_id=trace_id,
                system_prompt=system_prompt,
                tool_definitions=tool_definitions,
            )
        span_id = f"span_{uuid4().hex}"
        runtime_model = current_model_runtime()
        target_id = runtime_model.model if runtime_model is not None else self.settings.llm_model
        next_count = (plan.previous_state.count if plan.previous_state is not None else 0) + 1
        await self._observability.start_span(
            trace_id,
            span_id=span_id,
            parent_span_id=context.root_span_id,
            kind="internal",
            name="context.compress",
            target_id=target_id,
            input_data={
                "through_message_id": plan.through_message_id,
                "summarized_message_count": len(plan.messages_to_summarize),
            },
            attributes={
                "before_tokens": before_tokens,
                "threshold_tokens": threshold_tokens,
                "context_window_tokens": self.settings.context_window_tokens,
                "compression_count": next_count,
            },
        )
        await self._observability.record_event(
            trace_id,
            kind="context.compression.started",
            status="started",
            details={
                "before_tokens": before_tokens,
                "threshold_tokens": threshold_tokens,
                "summarized_message_count": len(plan.messages_to_summarize),
                "retained_message_count": len(plan.retained_messages),
                "compression_count": next_count,
            },
        )
        compression_context = ObservationContext(
            legacy_trace_id=trace_id,
            conversation_id=conversation.id,
            user_id=conversation.user_id,
            tenant_id=conversation.tenant_id,
            root_span_id=span_id,
        )
        with bind_observation_context(compression_context):
            result = await super()._prepare_context(
                conversation=conversation,
                trace_id=trace_id,
                system_prompt=system_prompt,
                tool_definitions=tool_definitions,
            )
        messages, input_tokens, output_tokens = result
        updated = await self.repository.get_conversation(conversation.id)
        compression_state = updated.config.get(COMPRESSION_CONFIG_KEY)
        succeeded = (
            isinstance(compression_state, dict)
            and compression_state.get("count") == next_count
            and compression_state.get("through_message_id") == plan.through_message_id
        )
        if not succeeded:
            error = {"type": "CompressionRejected", "message": "Context compression was not persisted"}
            await self._observability.finish_span(trace_id, span_id, "failed", error=error)
            await self._observability.record_event(
                trace_id,
                kind="context.compression.failed",
                status="failed",
                details={
                    "before_tokens": before_tokens,
                    "threshold_tokens": threshold_tokens,
                    "compression_count": next_count,
                    "error": error,
                },
            )
            return result

        assert isinstance(compression_state, dict)
        after_tokens = estimate_request_tokens(messages, tool_definitions)
        summary = str(compression_state.get("summary") or "")
        attributes = {
            "before_tokens": before_tokens,
            "after_tokens": after_tokens,
            "threshold_tokens": threshold_tokens,
            "context_window_tokens": self.settings.context_window_tokens,
            "summarized_message_count": len(plan.messages_to_summarize),
            "retained_message_count": len(plan.retained_messages),
            "compression_count": next_count,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        }
        await self._observability.finish_span(
            trace_id,
            span_id,
            "completed",
            output_data={
                "through_message_id": plan.through_message_id,
                "summary_length": len(summary),
            },
            attributes=attributes,
        )
        await self._observability.record_event(
            trace_id,
            kind="context.compacted",
            status="completed",
            details=attributes,
        )
        return result

    async def _aina_graph(
        self,
        conversation: Conversation,
        model_capabilities: dict[str, Capability],
    ) -> dict[str, Any]:
        records = sorted(
            await self.repository.list_ainas(),
            key=lambda item: item.manifest.aina.id,
        )
        installations = {
            item.aina_id: item
            for item in await self.repository.list_installations(
                tenant_id=conversation.tenant_id,
                user_id=conversation.user_id,
            )
        }
        available: list[dict[str, Any]] = []
        excluded: list[dict[str, Any]] = []
        for record in records:
            manifest = record.manifest
            aina_id = manifest.aina.id
            installation = installations.get(aina_id)
            is_available, reason, missing_permissions = _aina_availability(
                record,
                installation,
                conversation,
            )
            if not is_available:
                excluded.append(
                    {
                        "id": aina_id,
                        "name": manifest.aina.name,
                        "runtime": manifest.runtime.type,
                        "reason": reason,
                        "missing_permissions": missing_permissions,
                    }
                )
                continue
            owned_scope = {
                capability.capability_id: capability
                for capability in model_capabilities.values()
                if capability.owner_aina_id == aina_id
            }
            entrypoint = next(
                (
                    capability
                    for capability in model_capabilities.values()
                    if capability.owner_aina_id == aina_id
                    and capability.kind == "aina"
                    and capability.capability_id == aina_id
                ),
                None,
            )
            available.append(
                {
                    "id": aina_id,
                    "name": manifest.aina.name,
                    "version": manifest.aina.version,
                    "runtime": manifest.runtime.type,
                    "availability": reason,
                    "routing_candidate": entrypoint is not None,
                    "entrypoint": _capability_details(entrypoint) if entrypoint else None,
                    "capabilities": {
                        "skills": [
                            _manifest_capability_details(item, "skill", owned_scope)
                            for item in manifest.capabilities.skills
                        ],
                        "tools": [
                            _manifest_capability_details(item, "tool", owned_scope)
                            for item in manifest.capabilities.tools
                        ],
                        "ui": [
                            {
                                "id": item.id,
                                "kind": item.kind,
                                "description": item.description,
                            }
                            for item in manifest.capabilities.ui
                        ],
                        "events": manifest.capabilities.events,
                    },
                    "main_widget": (
                        {"id": manifest.main_widget.id, "kind": manifest.main_widget.kind}
                        if manifest.main_widget
                        else None
                    ),
                }
            )
        return {
            "available_count": len(available),
            "counts": {
                "builtin_aina": sum(item["runtime"] == "builtin" for item in available),
                "remote_aina": sum(item["runtime"] == "remote" for item in available),
            },
            "available": available,
            "excluded": excluded,
        }


def _error_details(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, PlatformError):
        return {
            "code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
        }
    return {"type": type(exc).__name__, "message": str(exc)}


def _forced_function(tool_choice: dict[str, Any] | str | None) -> str | None:
    if not isinstance(tool_choice, dict):
        return None
    function = tool_choice.get("function")
    return str(function.get("name")) if isinstance(function, dict) and function.get("name") else None


def _capability_version(capability: Capability) -> str | None:
    if capability.kind == "tool":
        return cast(ToolRecord, capability.value).version
    if capability.kind == "aina":
        aina, _installation = cast(tuple[AinaRecord, AinaInstallation], capability.value)
        return aina.manifest.aina.version
    return None


def _model_message_preview(message: dict[str, Any]) -> dict[str, Any]:
    preview = dict(message)
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        preview["tool_calls"] = [
            _tool_call_preview(call) for call in tool_calls if isinstance(call, dict)
        ]
    return preview


def _tool_call_preview(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function")
    if not isinstance(function, dict):
        return dict(call)
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return {
        "call_id": call.get("id"),
        "function_name": function.get("name"),
        "arguments": arguments,
    }


def _tool_call_details(
    call: dict[str, Any],
    capabilities: dict[str, Capability],
) -> dict[str, Any]:
    function = call.get("function") or {}
    function_name = str(function.get("name") or "")
    capability = capabilities.get(function_name)
    return {
        "call_id": str(call.get("id") or ""),
        "function_name": function_name,
        "capability_id": capability.capability_id if capability else None,
        "kind": capability.kind if capability else None,
        "arguments": _tool_call_preview(call).get("arguments"),
    }


def _capability_details(capability: Capability) -> dict[str, Any]:
    return {
        "id": capability.capability_id,
        "kind": capability.kind,
        "function_name": capability.function_name,
        "display_name": capability.display_name,
        "requires_confirmation": capability.requires_confirmation,
        "owner_aina_id": capability.owner_aina_id,
    }


def _model_scope_details(
    capabilities: dict[str, Capability],
    *,
    forced_capability: str | None,
    forced_function: str | None,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    standalone: list[dict[str, Any]] = []
    for capability in sorted(
        capabilities.values(),
        key=lambda item: (item.kind, item.capability_id),
    ):
        details = _capability_details(capability)
        if capability.owner_aina_id is None:
            standalone.append(details)
        else:
            grouped.setdefault(capability.owner_aina_id, []).append(details)
    return {
        "counts": {
            "remote_tool": sum(item.kind == "tool" for item in capabilities.values()),
            "remote_aina": sum(item.kind == "aina" for item in capabilities.values()),
            "builtin_capability": sum(item.kind == "builtin" for item in capabilities.values()),
        },
        "forced": forced_capability,
        "forced_function": forced_function,
        "by_aina": [
            {"aina_id": aina_id, "capabilities": grouped[aina_id]}
            for aina_id in sorted(grouped)
        ],
        "standalone": standalone,
    }


def _manifest_capability_details(
    capability: AinaCapability,
    kind: str,
    owned_scope: dict[str, Capability],
) -> dict[str, Any]:
    runtime_capability = owned_scope.get(capability.id)
    return {
        "id": capability.id,
        "kind": kind,
        "name": capability.name,
        "description": capability.description,
        "model_exposed": runtime_capability is not None,
        "function_name": runtime_capability.function_name if runtime_capability else None,
    }


def _aina_availability(
    record: AinaRecord,
    installation: AinaInstallation | None,
    conversation: Conversation,
) -> tuple[bool, str, list[str]]:
    manifest = record.manifest
    if record.status != "registered":
        return False, "disabled", []
    if manifest.runtime.type == "builtin":
        return True, "builtin", []
    if installation is None:
        return False, "not_installed", []
    if installation.status != "active":
        return False, "installation_disabled", []
    if conversation.enabled_ainas and manifest.aina.id not in conversation.enabled_ainas:
        return False, "disabled_for_conversation", []
    missing_permissions = sorted(
        set(manifest.permissions) - set(installation.granted_permissions)
    )
    if missing_permissions:
        return False, "missing_permissions", missing_permissions
    return True, "installed", []
