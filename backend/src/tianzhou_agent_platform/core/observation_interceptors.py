"""Decorators that observe stable application ports without business callbacks."""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any
from uuid import uuid4

from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.aina.builtin import invoke_builtin
from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.aina.document.task_service import DocumentEditTaskService
from tianzhou_agent_platform.aina.protocol.models import (
    AinaInstallation,
    AinaInvokeResponse,
    AinaManifest,
)
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.core.builtin_tools import invoke_platform_tool
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.chat import ApprovalRecord
from tianzhou_agent_platform.core.llm import EventSink, LLMClient, LLMResult
from tianzhou_agent_platform.core.model_settings import current_model_runtime
from tianzhou_agent_platform.core.observation_context import current_observation_context
from tianzhou_agent_platform.core.observability import ObservabilityAspect
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.sandbox.service import SandboxService


class ObservedLLMClient:
    """Observe model calls at the LLM port boundary."""

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
        except PlatformError as exc:
            await self._observability.finish_span(
                trace_id,
                observed_span_id,
                "failed",
                error={
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                },
            )
            raise
        except Exception as exc:
            await self._observability.finish_span(
                trace_id,
                observed_span_id,
                "failed",
                error={"type": type(exc).__name__, "message": str(exc)},
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
        return result

    async def aclose(self) -> None:
        close = getattr(self._delegate, "aclose", None)
        if close is not None:
            await close()


class AgentEventPublisher:
    """Business-event port backed by the observability adapter."""

    def __init__(self, observability: ObservabilityAspect) -> None:
        self._observability = observability

    async def emit(self, trace_id: str, **event: Any) -> None:
        await self._observability.record_event(trace_id, **event)

    async def cancelled_approvals(self, approvals: list[ApprovalRecord]) -> None:
        await self._observability.record_cancelled_approvals(approvals)


class AgentRunObserver:
    """Own agent trace/root lifecycle outside the business orchestrator."""

    def __init__(self, observability: ObservabilityAspect) -> None:
        self._observability = observability

    async def create(
        self,
        *,
        trace_id: str,
        root_span_id: str,
        conversation_id: str,
        user_id: str,
        tenant_id: str,
        input_data: Any,
        attributes: dict[str, Any],
    ) -> bool:
        return bool(
            await self._observability.create_agent_trace(
                trace_id=trace_id,
                root_span_id=root_span_id,
                conversation_id=conversation_id,
                user_id=user_id,
                tenant_id=tenant_id,
                input_data=input_data,
                attributes=attributes,
            )
        )

    async def ensure_root(
        self,
        trace_id: str,
        *,
        span_id: str,
        conversation_id: str,
    ) -> str:
        return await self._observability.ensure_agent_root_span(
            trace_id,
            span_id=span_id,
            conversation_id=conversation_id,
        )

    async def finish(
        self,
        trace_id: str,
        root_span_id: str,
        status: str,
        *,
        output_data: Any,
        attributes: dict[str, Any],
    ) -> None:
        if status != "approval_required":
            await self._observability.finish_span(
                trace_id,
                root_span_id,
                "completed" if status == "completed" else "failed",
                output_data=output_data,
                attributes=attributes,
            )
        await self._observability.finish_trace(trace_id, status)

    async def finish_trace(self, trace_id: str, status: str) -> None:
        await self._observability.finish_trace(trace_id, status)


class InternalOperationObserver:
    """Typed lifecycle port for semantic internal operations such as compression."""

    def __init__(self, observability: ObservabilityAspect) -> None:
        self._observability = observability

    async def start(self, trace_id: str, **span: Any) -> None:
        await self._observability.start_span(trace_id, **span)

    async def complete(
        self,
        trace_id: str,
        span_id: str,
        *,
        output_data: Any,
        attributes: dict[str, Any],
    ) -> None:
        await self._observability.finish_span(
            trace_id,
            span_id,
            "completed",
            output_data=output_data,
            attributes=attributes,
        )

    async def fail(self, trace_id: str, span_id: str, *, error: dict[str, Any]) -> None:
        await self._observability.finish_span(
            trace_id,
            span_id,
            "failed",
            error=error,
        )


class ObservedCapabilityGateway:
    """Observe actual remote/managed capability execution at the gateway port."""

    def __init__(
        self,
        delegate: RemoteCapabilityGateway,
        observability: ObservabilityAspect,
        *,
        repository: InMemoryRepository,
        document_service: DocumentService | None = None,
        document_edit_task_service: DocumentEditTaskService | None = None,
        sandbox_service: SandboxService | None = None,
    ) -> None:
        self._delegate = delegate
        self._observability = observability
        self._repository = repository
        self._document_service = document_service
        self._document_edit_task_service = document_edit_task_service
        self._sandbox_service = sandbox_service

    async def invoke_tool(
        self,
        tool: ToolRecord,
        *,
        arguments: dict[str, Any],
        call_id: str,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
        trace_id: str,
    ) -> tuple[Any, float]:
        context = current_observation_context()
        if context is None or context.legacy_trace_id != trace_id or context.root_span_id is None:
            return await self._delegate.invoke_tool(
                tool,
                arguments=arguments,
                call_id=call_id,
                user_id=user_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
            )
        span_id = f"span_{uuid4().hex}"
        await self._observability.start_span(
            trace_id,
            span_id=span_id,
            parent_span_id=context.root_span_id,
            kind="tool",
            name=tool.name,
            target_id=tool.tool_id,
            target_version=tool.version,
            logical_call_id=call_id,
            input_data=arguments,
        )
        try:
            result, duration_ms = await self._delegate.invoke_tool(
                tool,
                arguments=arguments,
                call_id=call_id,
                user_id=user_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
            )
        except Exception as exc:
            await self._finish_capability_error(trace_id, span_id, exc)
            raise
        await self._observability.finish_span(
            trace_id,
            span_id,
            "completed",
            input_data=arguments,
            output_data=result,
            attributes={
                "arguments": arguments,
                "result": result,
                "duration_ms": duration_ms,
            },
        )
        return result, duration_ms

    async def invoke_aina(
        self,
        manifest: AinaManifest,
        installation: AinaInstallation,
        *,
        arguments: dict[str, Any],
        call_id: str,
        conversation_id: str,
        trace_id: str,
        available_tools: list[str],
    ) -> tuple[AinaInvokeResponse, float]:
        context = current_observation_context()
        if context is None or context.legacy_trace_id != trace_id or context.root_span_id is None:
            return await self._delegate.invoke_aina(
                manifest,
                installation,
                arguments=arguments,
                call_id=call_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                available_tools=available_tools,
            )
        span_id = f"span_{uuid4().hex}"
        await self._observability.start_span(
            trace_id,
            span_id=span_id,
            parent_span_id=context.root_span_id,
            kind="aina",
            name=manifest.aina.name,
            target_id=manifest.aina.id,
            target_version=manifest.aina.version,
            logical_call_id=call_id,
            input_data=arguments,
        )
        try:
            response, duration_ms = await self._delegate.invoke_aina(
                manifest,
                installation,
                arguments=arguments,
                call_id=call_id,
                conversation_id=conversation_id,
                trace_id=trace_id,
                available_tools=available_tools,
            )
        except Exception as exc:
            await self._finish_capability_error(trace_id, span_id, exc)
            raise
        output = response.model_dump(mode="json")
        await self._observability.finish_span(
            trace_id,
            span_id,
            "completed",
            input_data=arguments,
            output_data=output,
            attributes={
                "arguments": arguments,
                "result": output,
                "duration_ms": duration_ms,
            },
        )
        return response, duration_ms

    async def _finish_capability_error(
        self,
        trace_id: str,
        span_id: str,
        exc: Exception,
    ) -> None:
        if isinstance(exc, PlatformError):
            error = {
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            }
        else:
            error = {"type": type(exc).__name__, "message": str(exc)}
        await self._observability.finish_span(
            trace_id,
            span_id,
            "failed",
            error=error,
        )

    async def invoke_platform(
        self,
        tool_id: str,
        *,
        function_name: str,
        arguments: dict[str, Any],
        call_id: str,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
        trace_id: str,
    ) -> tuple[Any, list[Any], float]:
        span_id = await self._start_local_span(
            trace_id=trace_id,
            call_id=call_id,
            function_name=function_name,
            target_id=tool_id,
            arguments=arguments,
        )
        started = perf_counter()
        try:
            result, widgets = await invoke_platform_tool(
                self._repository,
                tool_id,
                arguments,
                user_id=user_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
            )
        except Exception as exc:
            if span_id is not None:
                await self._finish_capability_error(trace_id, span_id, exc)
            raise
        duration_ms = (perf_counter() - started) * 1000
        if span_id is not None:
            await self._finish_local_span(
                trace_id, span_id, arguments, result, duration_ms
            )
        return result, widgets, duration_ms

    async def invoke_builtin(
        self,
        tool_id: str,
        *,
        function_name: str,
        arguments: dict[str, Any],
        call_id: str,
        user_id: str,
        tenant_id: str,
        conversation_id: str,
        trace_id: str,
    ) -> tuple[Any, list[Any], float]:
        span_id = await self._start_local_span(
            trace_id=trace_id,
            call_id=call_id,
            function_name=function_name,
            target_id=tool_id,
            arguments=arguments,
        )
        started = perf_counter()
        try:
            result, widgets = await invoke_builtin(
                self._repository,
                tool_id,
                arguments,
                user_id=user_id,
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                document_service=self._document_service,
                document_edit_task_service=self._document_edit_task_service,
                sandbox_service=self._sandbox_service,
            )
        except Exception as exc:
            if span_id is not None:
                await self._finish_capability_error(trace_id, span_id, exc)
            raise
        duration_ms = (perf_counter() - started) * 1000
        if span_id is not None:
            await self._finish_local_span(
                trace_id, span_id, arguments, result, duration_ms
            )
        return result, widgets, duration_ms

    async def _start_local_span(
        self,
        *,
        trace_id: str,
        call_id: str,
        function_name: str,
        target_id: str,
        arguments: dict[str, Any],
    ) -> str | None:
        context = current_observation_context()
        if context is None or context.legacy_trace_id != trace_id or context.root_span_id is None:
            return None
        span_id = f"span_{uuid4().hex}"
        await self._observability.start_span(
            trace_id,
            span_id=span_id,
            parent_span_id=context.root_span_id,
            kind="tool",
            name=function_name,
            target_id=target_id,
            logical_call_id=call_id,
            input_data=arguments,
        )
        return span_id

    async def _finish_local_span(
        self,
        trace_id: str,
        span_id: str,
        arguments: dict[str, Any],
        result: Any,
        duration_ms: float,
    ) -> None:
        await self._observability.finish_span(
            trace_id,
            span_id,
            "completed",
            input_data=arguments,
            output_data=result,
            attributes={
                "arguments": arguments,
                "result": result,
                "duration_ms": duration_ms,
            },
        )


def _model_message_preview(message: dict[str, Any]) -> dict[str, Any]:
    preview = dict(message)
    tool_calls = message.get("tool_calls")
    if not isinstance(tool_calls, list):
        return preview
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
