"""Capability-call policy and observation around native tool execution.

``CapabilityCallMiddleware`` wraps each logical tool call once, outside native retries: it enforces the run's
capability scope and argument schema, records the tool span and trace events, streams ``tool.requested`` /
``tool.completed`` to the client, and turns failures into the product's model-visible error envelope.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any, NoReturn
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.errors import GraphBubbleUp

from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.services.agent_integration.capabilities import (
    Capability,
    capability_version,
    scope_recovery,
    tool_error_payload,
)
from tianzhou_agent_platform.services.agent_integration.capability_tools import (
    CapabilityToolError,
    validate_capability_args,
)
from tianzhou_agent_platform.services.agent_integration.run import AgentRun

# Larger results are left off live tool events; the UI shows them after the turn reloads.
_LIVE_TOOL_RESULT_MAX_BYTES = 16_000


class CapabilityFailures:
    """Records failed capability calls once, as ``<kind>.failed`` trace events and live error events."""

    def __init__(self, run: AgentRun, recovery_capabilities: dict[str, Capability]) -> None:
        self.run = run
        self.recovery_capabilities = recovery_capabilities

    async def record(
        self,
        capability: Capability | None,
        *,
        call_id: str,
        function_name: str,
        payload: dict[str, Any],
    ) -> None:
        kind = capability.kind if capability is not None else "tool"
        error = payload["error"]
        self.run.scope.recorded_failures.add(call_id)
        await self.run.push(
            f"{kind}.failed",
            "failed",
            target_type=kind if capability is not None else "capability",
            target_id=capability.capability_id if capability is not None else function_name,
            details={
                "call_id": call_id,
                "function_name": function_name,
                "code": error["code"],
                "message": error["message"],
                "retryable": error.get("retryable", False),
                "recovery": payload.get("recovery"),
            },
        )
        await self.run.emit({"type": "error", "code": error["code"], "source": "capability"})

    async def fail(
        self,
        capability: Capability | None,
        *,
        call_id: str,
        function_name: str,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> NoReturn:
        payload = tool_error_payload(code, message, retryable=retryable)
        await self.record(capability, call_id=call_id, function_name=function_name, payload=payload)
        raise CapabilityToolError(payload)

    async def scope_miss(self, name: str, call_id: str) -> dict[str, Any]:
        """Error payload for a call outside the visible scope, pointing at the owning AINA when there is one."""
        recovery = scope_recovery(name, self.recovery_capabilities)
        payload = tool_error_payload(
            "CAPABILITY_SCOPE_REQUIRED" if recovery else "RESOURCE_NOT_FOUND",
            (
                f"Capability {name!r} belongs to AINA {recovery['owner_aina_id']!r}, which is not "
                "active in the current scope."
                if recovery
                else f"Capability {name!r} is unavailable."
            ),
            recovery=recovery,
        )
        await self.record(
            recovery["capability"] if recovery else None,
            call_id=call_id,
            function_name=name,
            payload=payload,
        )
        return payload

    async def unknown_tool(self, name: str, call_id: str) -> str:
        return json.dumps(await self.scope_miss(name, call_id), ensure_ascii=False)

    async def invalid_arguments(self, call: Any) -> str:
        """Result of a call whose arguments were not valid JSON (it never reaches the capability)."""
        name = str(call.get("name") or "")
        payload = tool_error_payload(
            "INVALID_REQUEST",
            f"Capability {name} arguments are not valid JSON: {call.get('error') or 'unparseable arguments'}",
        )
        await self.record(
            self.run.registry.get(name),
            call_id=str(call.get("id") or ""),
            function_name=name,
            payload=payload,
        )
        return json.dumps(payload, ensure_ascii=False)

    async def unrecorded(self, name: str, call_id: str, payload: dict[str, Any]) -> None:
        """Record a failed result the capability wrapper never saw (e.g. blocked by the attempt budget)."""
        await self.record(self.run.registry.get(name), call_id=call_id, function_name=name, payload=payload)


class CapabilityCallMiddleware(AgentMiddleware):
    def __init__(self, run: AgentRun, failures: CapabilityFailures) -> None:
        super().__init__()
        self.run = run
        self.failures = failures

    async def awrap_tool_call(
        self,
        request: Any,
        handler: Callable[[Any], Awaitable[ToolMessage | Any]],
    ) -> ToolMessage | Any:
        run = self.run
        name = str(request.tool_call.get("name") or "")
        call_id = str(request.tool_call.get("id") or "")
        arguments = request.tool_call.get("args") or {}
        capability = run.registry.get(name)
        if capability is None:
            return await handler(request)
        if name not in run.scope.visible:
            raise CapabilityToolError(await self.failures.scope_miss(name, call_id))
        try:
            validate_capability_args(input_schema=capability.input_schema, args=arguments, function_name=name)
        except PlatformError as exc:
            await self.failures.fail(capability, call_id=call_id, function_name=name, code=exc.code, message=exc.message)
        if capability.kind == "aina":
            if run.scope.activated_in_batch:
                await self.failures.fail(
                    capability,
                    call_id=call_id,
                    function_name=name,
                    code="CONFLICT",
                    message="Only one AINA scope can be activated per model response.",
                )
            run.scope.activated_in_batch = True

        span_id = f"span_{uuid4().hex}"
        await run.push(
            f"{capability.kind}.requested",
            "started",
            target_type=capability.kind,
            target_id=capability.capability_id,
            details={
                "call_id": call_id,
                "function_name": name,
                "argument_fields": sorted(arguments),
                "arguments": arguments,
            },
        )
        await run.events.start_span(
            run.trace_id,
            span_id=span_id,
            kind="aina" if capability.kind == "aina" else "tool",
            name=name,
            target_id=capability.capability_id,
            target_version=capability_version(capability),
            logical_call_id=call_id,
            input_data=arguments,
        )
        live = {"kind": capability.kind, "id": capability.capability_id, "call_id": call_id, "name": name}
        await run.emit(
            {"type": "tool.requested", **live, "arguments": json.dumps(arguments, ensure_ascii=False, default=str)}
        )
        widgets_before = len(run.scope.widgets)
        started = perf_counter()
        try:
            message = await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:  # noqa: BLE001 - every capability failure is returned to the model
            code, text, retryable = _failure(capability, exc)
            await run.events.finish_span(
                run.trace_id,
                span_id,
                "failed",
                error={"code": code, "message": text, "retryable": getattr(exc, "retryable", False)},
            )
            await run.emit(
                {
                    "type": "tool.completed",
                    **live,
                    "status": "failed",
                    "result": json.dumps({"error": {"code": code, "message": text}}, ensure_ascii=False),
                }
            )
            await self.failures.fail(
                capability, call_id=call_id, function_name=name, code=code, message=text, retryable=retryable
            )
        if isinstance(message, ToolMessage):
            run.finished_tool_results[call_id] = message
        duration_ms = (perf_counter() - started) * 1000
        content = message.content if isinstance(message.content, str) else str(message.content)
        try:
            result = json.loads(content)
        except ValueError:
            result = content
        result_size_bytes = len(content.encode("utf-8"))
        widgets = [{"id": widget.id, "kind": widget.kind} for widget in run.scope.widgets[widgets_before:]]
        await run.events.finish_span(
            run.trace_id,
            span_id,
            "completed",
            input_data=arguments,
            output_data=result,
            attributes={
                "arguments": arguments,
                "result": result,
                "duration_ms": duration_ms,
                "result_size_bytes": result_size_bytes,
                "widgets": widgets,
            },
        )
        await run.push(
            f"{capability.kind}.completed",
            "completed",
            target_type=capability.kind,
            target_id=capability.capability_id,
            duration_ms=duration_ms,
            details={
                "call_id": call_id,
                "function_name": name,
                "result": result,
                "result_size_bytes": result_size_bytes,
                "widgets": widgets,
            },
        )
        await run.emit(
            {
                "type": "tool.completed",
                **live,
                "status": "completed",
                "result": content if result_size_bytes <= _LIVE_TOOL_RESULT_MAX_BYTES else None,
            }
        )
        return message


def _failure(capability: Capability, exc: Exception) -> tuple[str, str, bool]:
    """(code, message, model may retry) of a failed capability call."""
    if isinstance(exc, PlatformError):
        # Only transient failures of side-effect-free remote tools may be retried by the model.
        return exc.code, exc.message, exc.retryable and capability.retries_transiently
    if isinstance(exc, (TypeError, ValueError)):
        if capability.kind == "aina":
            return "DEPENDENCY_FAILED", f"The AINA returned invalid data: {exc}", False
        return "INVALID_REQUEST", f"Capability arguments produced invalid data: {exc}", False
    return "DEPENDENCY_FAILED", str(exc) or type(exc).__name__, False
