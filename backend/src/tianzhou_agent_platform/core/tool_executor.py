"""Tool execution engine: runs a batch of tool calls for the agent loop.

``ToolExecutor`` owns the per-response execution loop (approval gating,
deduplication, schema validation, capability dispatch, observability and
error isolation). It is wired by ``AgentRuntime`` with its dependencies so
the agent class no longer carries this concern inline.
"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Literal, TypedDict, cast
from uuid import uuid4

from tianzhou_agent_platform.aina.builtin import invoke_builtin
from tianzhou_agent_platform.aina.document.builtin import CREATE_EDIT_TASK_TOOL_ID
from tianzhou_agent_platform.core.builtin_tools import (
    DESCRIBE_AINA_TOOL_ID,
    OPEN_AINA_TOOL_ID,
    PLATFORM_TOOL_IDS,
    invoke_platform_tool,
)
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.core.capability import Capability
from tianzhou_agent_platform.core.chat import ApprovalRecord
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.llm import EventSink
from tianzhou_agent_platform.core.tool_execution import (
    ToolCall,
    _capability_scope_recovery,
    _capability_version,
    _tool_arguments_trace_data,
    _tool_call_trace_details,
    call_signature,
    collect_approval_required,
    decode_arguments,
    tool_output_message,
    truncate_tool_output,
    validate_call_arguments,
)
from tianzhou_agent_platform.aina.memory.models import MemoryRecord


class AgentState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    capabilities: dict[str, Capability]
    recovery_capabilities: dict[str, Capability]
    tool_definitions: list[dict[str, Any]]
    trace_id: str
    root_span_id: str
    conversation_id: str
    user_id: str
    tenant_id: str
    iterations: int
    max_iterations: int
    forced_function: str | None
    approved_call_ids: set[str]
    resume: bool
    event_sink: EventSink | None
    usage_input: int
    usage_output: int
    final_content: str
    final_status: Literal["completed", "approval_required", "failed"]
    approval: ApprovalRecord | None
    call_counts: dict[str, int]
    tool_span_ids: dict[str, str]
    widgets: list[WidgetDefinition]
    memory_context: list[MemoryRecord]


class ToolExecutor:
    """Executes one batch of assistant tool calls within a single agent turn."""

    def __init__(
        self,
        *,
        repository: Any,
        observability: Any,
        gateway: Any,
        document_service: Any,
        document_edit_task_service: Any,
        sandbox_service: Any,
        emit: Any,
        append_tool_error: Any,
        activate_builtin_aina_scope: Any,
        activate_aina_model_scope: Any,
    ) -> None:
        self.repository = repository
        self.observability = observability
        self.gateway = gateway
        self.document_service = document_service
        self.document_edit_task_service = document_edit_task_service
        self.sandbox_service = sandbox_service
        self._emit = emit
        self._append_tool_error = append_tool_error
        self._activate_builtin_aina_scope = activate_builtin_aina_scope
        self._activate_aina_model_scope = activate_aina_model_scope

    async def execute(
        self,
        state: AgentState,
        *,
        tool_calls: list[ToolCall],
    ) -> AgentState:
        messages = list(state["messages"])
        capabilities = state["capabilities"]
        approved = state.get("approved_call_ids", set())
        risky_calls, risky_names = collect_approval_required(tool_calls, capabilities, approved)
        if risky_calls:
            approval = ApprovalRecord(
                id=f"approval_{uuid4().hex}",
                conversation_id=state["conversation_id"],
                user_id=state["user_id"],
                tenant_id=state["tenant_id"],
                trace_id=state["trace_id"],
                tool_calls=tool_calls,
                capability_names=risky_names,
            )
            await self.repository.create_approval(approval)
            await self.observability.record_event(
                state["trace_id"],
                kind="approval.required",
                status="pending",
                target_type="capability",
                details={
                    "approval_id": approval.id,
                    "capabilities": risky_names,
                    "calls": [_tool_call_trace_details(call, capabilities) for call in risky_calls],
                },
            )
            await self._emit(
                state,
                {
                    "type": "approval.required",
                    "approval_id": approval.id,
                    "capabilities": risky_names,
                },
            )
            return {
                **state,
                "approval": approval,
                "final_content": f"Approval is required before running: {', '.join(risky_names)}.",
                "final_status": "approval_required",
            }

        call_counts = dict(state.get("call_counts", {}))
        widgets = list(state.get("widgets", []))
        async_task_message: str | None = None
        tool_failed = False
        next_capabilities = capabilities
        scope_activated = False
        available_tool_ids = [
            capability.capability_id for capability in capabilities.values() if capability.kind == "tool"
        ]
        for call in tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            call_id = str(call.get("id") or f"call_{uuid4().hex}")
            arguments_text = function.get("arguments") or "{}"
            capability = capabilities.get(name)
            tool_span_id = f"span_{uuid4().hex}"
            state.setdefault("tool_span_ids", {})[call_id] = tool_span_id
            await self.observability.start_span(
                state["trace_id"],
                span_id=tool_span_id,
                parent_span_id=state["root_span_id"],
                kind="aina" if capability is not None and capability.kind == "aina" else "tool",
                name=name or "unknown",
                target_id=capability.capability_id if capability is not None else name or None,
                target_version=_capability_version(capability),
                logical_call_id=call_id,
                input_data=_tool_arguments_trace_data(arguments_text),
                attributes={"function_name": name},
            )
            if capability is None:
                tool_failed = True
                recovery = _capability_scope_recovery(
                    name,
                    state.get("recovery_capabilities", {}),
                )
                await self._append_tool_error(
                    state,
                    messages,
                    call_id=call_id,
                    name=name,
                    code="CAPABILITY_SCOPE_REQUIRED" if recovery else "RESOURCE_NOT_FOUND",
                    message=(
                        f"Capability {name!r} belongs to AINA {recovery['owner_aina_id']!r}, which is not "
                        "active in the current scope."
                        if recovery
                        else f"Capability {name!r} is unavailable."
                    ),
                    capability=recovery["capability"] if recovery else None,
                    recovery=recovery,
                )
                continue
            try:
                arguments = decode_arguments(arguments_text)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                tool_failed = True
                await self._append_tool_error(
                    state,
                    messages,
                    call_id=call_id,
                    name=name,
                    code="INVALID_REQUEST",
                    message=f"Invalid JSON arguments: {exc}",
                )
                continue

            signature = call_signature(name, arguments)
            call_counts[signature] = call_counts.get(signature, 0) + 1
            if call_counts[signature] > 1:
                tool_failed = True
                await self._append_tool_error(
                    state,
                    messages,
                    call_id=call_id,
                    name=name,
                    code="CONFLICT",
                    message="The same capability call was already attempted in this run.",
                )
                continue

            try:
                validate_call_arguments(capability, arguments)
            except PlatformError as exc:
                tool_failed = True
                await self._append_tool_error(
                    state,
                    messages,
                    call_id=call_id,
                    name=name,
                    code=exc.code,
                    message=exc.message,
                    capability=capability,
                )
                continue
            except (TypeError, ValueError) as exc:
                tool_failed = True
                await self._append_tool_error(
                    state,
                    messages,
                    call_id=call_id,
                    name=name,
                    code="INVALID_REQUEST",
                    message=f"Capability arguments failed validation: {exc}",
                    capability=capability,
                )
                continue

            if capability.kind == "aina":
                aina, _installation = cast(tuple[AinaRecord, AinaInstallation], capability.value)
                if scope_activated:
                    tool_failed = True
                    await self._append_tool_error(
                        state,
                        messages,
                        call_id=call_id,
                        name=name,
                        code="CONFLICT",
                        message="Only one AINA scope can be activated per model response.",
                        capability=capability,
                    )
                    continue
                if aina.manifest.runtime.type == "builtin":
                    next_capabilities = await self._activate_builtin_aina_scope(
                        state,
                        capability=capability,
                        call_id=call_id,
                        function_name=name,
                        arguments=arguments,
                        messages=messages,
                    )
                    await self.observability.finish_span(
                        state["trace_id"],
                        tool_span_id,
                        "completed",
                        input_data=arguments,
                        output_data={"activated": True},
                        attributes={
                            "arguments": arguments,
                            "activated": True,
                        },
                    )
                    scope_activated = True
                    continue

            await self.observability.record_event(
                state["trace_id"],
                kind=f"{capability.kind}.requested",
                status="started",
                target_type=capability.kind,
                target_id=capability.capability_id,
                details={
                    "call_id": call_id,
                    "function_name": name,
                    "argument_fields": sorted(arguments),
                    "arguments": arguments,
                },
            )
            await self._emit(
                state,
                {"type": "tool.requested", "kind": capability.kind, "id": capability.capability_id},
            )
            try:
                call_started = perf_counter()
                if capability.capability_id in {DESCRIBE_AINA_TOOL_ID, OPEN_AINA_TOOL_ID}:
                    widgets = [widget for widget in widgets if widget.kind != "app_list"]
                widgets_before = len(widgets)
                if capability.kind == "tool":
                    tool = cast(ToolRecord, capability.value)
                    result, duration_ms = await self.gateway.invoke_tool(
                        tool,
                        arguments=arguments,
                        call_id=call_id,
                        user_id=state["user_id"],
                        tenant_id=state["tenant_id"],
                        conversation_id=state["conversation_id"],
                        trace_id=state["trace_id"],
                    )
                    result_payload: Any = result
                elif capability.kind == "aina":
                    aina, installation = cast(tuple[AinaRecord, AinaInstallation], capability.value)
                    response, duration_ms = await self.gateway.invoke_aina(
                        aina.manifest,
                        installation,
                        arguments=arguments,
                        call_id=call_id,
                        conversation_id=state["conversation_id"],
                        trace_id=state["trace_id"],
                        available_tools=available_tool_ids,
                    )
                    result_payload = response.model_dump(mode="json")
                    for output in response.outputs:
                        if output.type == "widget":
                            try:
                                widgets.append(WidgetDefinition.model_validate(output.content))
                            except ValueError as exc:
                                raise PlatformError(
                                    "DEPENDENCY_FAILED",
                                    "AINA returned an invalid widget output",
                                    status_code=502,
                                    source="aina",
                                ) from exc
                    next_capabilities = await self._activate_aina_model_scope(
                        state,
                        capability=capability,
                        call_id=call_id,
                        function_name=name,
                        arguments=arguments,
                        messages=messages,
                    )
                    scope_activated = True
                else:
                    if capability.capability_id in PLATFORM_TOOL_IDS:
                        result_payload, produced_widgets = await invoke_platform_tool(
                            self.repository,
                            cast(str, capability.value),
                            arguments,
                            user_id=state["user_id"],
                            tenant_id=state["tenant_id"],
                            conversation_id=state["conversation_id"],
                        )
                    else:
                        result_payload, produced_widgets = await invoke_builtin(
                            self.repository,
                            cast(str, capability.value),
                            arguments,
                            user_id=state["user_id"],
                            tenant_id=state["tenant_id"],
                            conversation_id=state["conversation_id"],
                            document_service=self.document_service,
                            document_edit_task_service=self.document_edit_task_service,
                            sandbox_service=self.sandbox_service,
                        )
                    widgets.extend(produced_widgets)
                    duration_ms = (perf_counter() - call_started) * 1000
                content = json.dumps(result_payload, ensure_ascii=False, default=str)
                result_size_bytes = len(content.encode("utf-8"))
                content = truncate_tool_output(content)
                messages.append(tool_output_message(name, call_id, content))
                await self.observability.record_event(
                    state["trace_id"],
                    kind=f"{capability.kind}.completed",
                    status="completed",
                    target_type=capability.kind,
                    target_id=capability.capability_id,
                    duration_ms=duration_ms,
                    details={
                        "call_id": call_id,
                        "function_name": name,
                        "result": result_payload,
                        "result_size_bytes": result_size_bytes,
                        "widgets": [
                            {"id": widget.id, "kind": widget.kind} for widget in widgets[widgets_before:]
                        ],
                    },
                )
                await self.observability.finish_span(
                    state["trace_id"],
                    tool_span_id,
                    "completed",
                    input_data=arguments,
                    output_data=result_payload,
                    attributes={
                        "arguments": arguments,
                        "result": result_payload,
                        "result_size_bytes": result_size_bytes,
                    },
                )
                await self._emit(
                    state,
                    {"type": "tool.completed", "kind": capability.kind, "id": capability.capability_id},
                )
                if capability.capability_id == CREATE_EDIT_TASK_TOOL_ID:
                    task = result_payload.get("task") if isinstance(result_payload, dict) else None
                    if isinstance(task, dict):
                        title = str(task.get("title") or "文档修改任务")
                        sections = task.get("sections")
                        section_count = len(sections) if isinstance(sections, list) else 0
                        async_task_message = (
                            f'修改任务“{title}”已创建，AI 正在后台处理 {section_count} 个章节。'
                            "完成后会进入待检视状态，请在右侧“任务”模式查看进度和草稿。"
                            "正式文档会在您确认合并后才更新。"
                        )
            except PlatformError as exc:
                tool_failed = True
                await self._append_tool_error(
                    state,
                    messages,
                    call_id=call_id,
                    name=name,
                    code=exc.code,
                    message=exc.message,
                    capability=capability,
                )
            except (TypeError, ValueError) as exc:
                tool_failed = True
                dependency_failure = capability.kind == "aina"
                await self._append_tool_error(
                    state,
                    messages,
                    call_id=call_id,
                    name=name,
                    code="DEPENDENCY_FAILED" if dependency_failure else "INVALID_REQUEST",
                    message=(
                        f"The AINA returned invalid data: {exc}"
                        if dependency_failure
                        else f"Capability arguments produced invalid data: {exc}"
                    ),
                    capability=capability,
                )

        update: AgentState = {
            **state,
            "messages": messages,
            "call_counts": call_counts,
            "approval": None,
            "widgets": widgets,
            "capabilities": next_capabilities,
            "tool_definitions": [item.llm_definition() for item in next_capabilities.values()],
        }
        if async_task_message is not None and not tool_failed:
            messages.append({"role": "assistant", "content": async_task_message})
            update["messages"] = messages
            update["final_content"] = async_task_message
            update["final_status"] = "completed"
        elif state.get("iterations", 0) >= state["max_iterations"]:
            limit_message = (
                f"I stopped after {state['max_iterations']} model iterations because the capability loop "
                "did not produce a final answer."
            )
            messages.append({"role": "assistant", "content": limit_message})
            update["messages"] = messages
            update["final_content"] = limit_message
            update["final_status"] = "failed"
        return update
