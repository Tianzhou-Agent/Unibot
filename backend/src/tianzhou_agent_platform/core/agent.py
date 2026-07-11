from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal, TypedDict, cast
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.llm import EventSink, LLMClient
from tianzhou_agent_platform.core.models import (
    AinaInstallation,
    AinaRecord,
    ApprovalRecord,
    ChatRequest,
    ChatResponse,
    Conversation,
    ConversationCreate,
    ToolRecord,
    TraceEvent,
    TraceRecord,
    Usage,
)
from tianzhou_agent_platform.core.repository import InMemoryRepository

_HIGH_RISK_MARKERS = (
    "send",
    "email",
    "publish",
    "delete",
    "payment",
    "pay.",
    "order",
    "irreversible",
    "sensitive.third_party",
)


@dataclass(slots=True)
class Capability:
    kind: Literal["tool", "aina"]
    capability_id: str
    function_name: str
    display_name: str
    description: str
    input_schema: dict[str, Any]
    requires_confirmation: bool
    value: ToolRecord | tuple[AinaRecord, AinaInstallation]

    def llm_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.function_name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class AgentState(TypedDict, total=False):
    messages: list[dict[str, Any]]
    capabilities: dict[str, Capability]
    tool_definitions: list[dict[str, Any]]
    trace_id: str
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


class AgentRuntime:
    """Bounded OpenAI tool-calling loop adapted from Hermes's core turn shape.

    The MVP keeps the useful invariants from Hermes: persistable OpenAI-wire
    messages, a hard iteration budget, a tool result for every tool call,
    invalid-argument recovery through the model, and capability failure
    isolation. LangGraph supplies the model -> tools -> model state machine.
    """

    def __init__(
        self,
        *,
        settings: AgentSettings,
        repository: InMemoryRepository,
        llm: LLMClient,
        gateway: RemoteCapabilityGateway,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.llm = llm
        self.gateway = gateway
        graph = StateGraph(AgentState)
        graph.add_node("model", self._model_node)
        graph.add_node("tools", self._tool_node)
        graph.add_conditional_edges(START, self._entry_route, {"model": "model", "tools": "tools"})
        graph.add_conditional_edges("model", self._after_model, {"tools": "tools", "end": END})
        graph.add_conditional_edges("tools", self._after_tools, {"model": "model", "end": END})
        self._graph = graph.compile()

    @staticmethod
    def _entry_route(state: AgentState) -> str:
        return "tools" if state.get("resume") else "model"

    @staticmethod
    def _after_model(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if last.get("tool_calls") else "end"

    @staticmethod
    def _after_tools(state: AgentState) -> str:
        if state.get("approval") is not None or state.get("final_status") == "failed":
            return "end"
        return "model"

    async def _emit(self, state: AgentState, event: dict[str, Any]) -> None:
        sink = state.get("event_sink")
        if sink is not None:
            await sink(event)

    async def _model_node(self, state: AgentState) -> AgentState:
        iterations = state.get("iterations", 0) + 1
        started = perf_counter()
        await self.repository.add_trace_event(
            state["trace_id"],
            TraceEvent(
                kind="model.requested",
                status="started",
                target_type="model",
                target_id=self.settings.llm_model,
                details={"iteration": iterations},
            ),
        )
        tool_choice: dict[str, Any] | str | None = None
        if iterations == 1 and state.get("forced_function"):
            tool_choice = {
                "type": "function",
                "function": {"name": state["forced_function"]},
            }
        try:
            result = await self.llm.complete(
                messages=state["messages"],
                tools=state["tool_definitions"],
                tool_choice=tool_choice,
                event_sink=state.get("event_sink"),
            )
        except PlatformError as exc:
            await self.repository.add_trace_event(
                state["trace_id"],
                TraceEvent(
                    kind="model.failed",
                    status="failed",
                    target_type="model",
                    target_id=self.settings.llm_model,
                    duration_ms=(perf_counter() - started) * 1000,
                    details={"code": exc.code},
                ),
            )
            raise

        message = result.message
        if not message.get("content") and not message.get("tool_calls"):
            message = {"role": "assistant", "content": "The model returned an empty response."}
            final_status: Literal["completed", "approval_required", "failed"] = "failed"
        else:
            final_status = "completed"
        messages = [*state["messages"], message]
        await self.repository.add_trace_event(
            state["trace_id"],
            TraceEvent(
                kind="model.completed",
                status="completed",
                target_type="model",
                target_id=self.settings.llm_model,
                duration_ms=(perf_counter() - started) * 1000,
                details={
                    "iteration": iterations,
                    "finish_reason": result.finish_reason,
                    "tool_call_count": len(message.get("tool_calls") or []),
                },
            ),
        )
        update: AgentState = {
            **state,
            "messages": messages,
            "iterations": iterations,
            "usage_input": state.get("usage_input", 0) + result.input_tokens,
            "usage_output": state.get("usage_output", 0) + result.output_tokens,
        }
        if not message.get("tool_calls"):
            update["final_content"] = message.get("content") or ""
            update["final_status"] = final_status
        return update

    async def _tool_node(self, state: AgentState) -> AgentState:
        messages = list(state["messages"])
        assistant = next(
            (item for item in reversed(messages) if item.get("role") == "assistant" and item.get("tool_calls")),
            None,
        )
        if assistant is None:
            raise PlatformError("INTERNAL_ERROR", "No assistant tool-call message is available", status_code=500)
        tool_calls = assistant.get("tool_calls") or []
        capabilities = state["capabilities"]
        approved = state.get("approved_call_ids", set())
        risky_calls = []
        risky_names = []
        for call in tool_calls:
            capability = capabilities.get((call.get("function") or {}).get("name", ""))
            if capability and capability.requires_confirmation and call.get("id") not in approved:
                risky_calls.append(call)
                risky_names.append(capability.display_name)
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
            await self.repository.add_trace_event(
                state["trace_id"],
                TraceEvent(
                    kind="approval.required",
                    status="pending",
                    target_type="capability",
                    details={"approval_id": approval.id, "capabilities": risky_names},
                ),
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
        available_tool_ids = [
            capability.capability_id for capability in capabilities.values() if capability.kind == "tool"
        ]
        for call in tool_calls:
            function = call.get("function") or {}
            name = str(function.get("name") or "")
            call_id = str(call.get("id") or f"call_{uuid4().hex}")
            arguments_text = function.get("arguments") or "{}"
            capability = capabilities.get(name)
            signature = hashlib.sha256(f"{name}:{arguments_text}".encode()).hexdigest()
            call_counts[signature] = call_counts.get(signature, 0) + 1
            if call_counts[signature] > 2:
                await self._append_tool_error(
                    state,
                    messages,
                    call_id=call_id,
                    name=name,
                    code="CONFLICT",
                    message="The same capability call was repeated too many times.",
                )
                continue
            if capability is None:
                await self._append_tool_error(
                    state,
                    messages,
                    call_id=call_id,
                    name=name,
                    code="RESOURCE_NOT_FOUND",
                    message=f"Capability {name!r} is unavailable.",
                )
                continue
            try:
                arguments = json.loads(arguments_text) if isinstance(arguments_text, str) else arguments_text
                if not isinstance(arguments, dict):
                    raise ValueError("arguments must decode to an object")
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                await self._append_tool_error(
                    state,
                    messages,
                    call_id=call_id,
                    name=name,
                    code="INVALID_REQUEST",
                    message=f"Invalid JSON arguments: {exc}",
                )
                continue

            await self.repository.add_trace_event(
                state["trace_id"],
                TraceEvent(
                    kind=f"{capability.kind}.requested",
                    status="started",
                    target_type=capability.kind,
                    target_id=capability.capability_id,
                    details={"argument_fields": sorted(arguments)},
                ),
            )
            await self._emit(
                state,
                {"type": "tool.requested", "kind": capability.kind, "id": capability.capability_id},
            )
            try:
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
                else:
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
                content = json.dumps(result_payload, ensure_ascii=False, default=str)
                if len(content) > 50_000:
                    content = f"{content[:50_000]}\n[tool output truncated]"
                messages.append(
                    {
                        "role": "tool",
                        "name": name,
                        "tool_call_id": call_id,
                        "content": content,
                    }
                )
                await self.repository.add_trace_event(
                    state["trace_id"],
                    TraceEvent(
                        kind=f"{capability.kind}.completed",
                        status="completed",
                        target_type=capability.kind,
                        target_id=capability.capability_id,
                        duration_ms=duration_ms,
                    ),
                )
                await self._emit(
                    state,
                    {"type": "tool.completed", "kind": capability.kind, "id": capability.capability_id},
                )
            except PlatformError as exc:
                await self._append_tool_error(
                    state,
                    messages,
                    call_id=call_id,
                    name=name,
                    code=exc.code,
                    message=exc.message,
                    capability=capability,
                )

        update: AgentState = {**state, "messages": messages, "call_counts": call_counts, "approval": None}
        if state.get("iterations", 0) >= state["max_iterations"]:
            limit_message = (
                f"I stopped after {state['max_iterations']} model iterations because the capability loop "
                "did not produce a final answer."
            )
            messages.append({"role": "assistant", "content": limit_message})
            update["messages"] = messages
            update["final_content"] = limit_message
            update["final_status"] = "failed"
        return update

    async def _append_tool_error(
        self,
        state: AgentState,
        messages: list[dict[str, Any]],
        *,
        call_id: str,
        name: str,
        code: str,
        message: str,
        capability: Capability | None = None,
    ) -> None:
        payload = {"error": {"code": code, "message": message, "retryable": code in {"TIMEOUT", "RATE_LIMITED"}}}
        messages.append(
            {
                "role": "tool",
                "name": name,
                "tool_call_id": call_id,
                "content": json.dumps(payload, ensure_ascii=False),
            }
        )
        await self.repository.add_trace_event(
            state["trace_id"],
            TraceEvent(
                kind=f"{capability.kind if capability else 'tool'}.failed",
                status="failed",
                target_type=capability.kind if capability else "capability",
                target_id=capability.capability_id if capability else name,
                details={"code": code},
            ),
        )
        await self._emit(state, {"type": "error", "code": code, "source": "capability"})

    async def chat(self, request: ChatRequest, *, event_sink: EventSink | None = None) -> ChatResponse:
        if request.conversation_id is None:
            conversation = await self.repository.create_conversation(
                ConversationCreate(user_id=request.user_id, tenant_id=request.tenant_id)
            )
        else:
            conversation = await self.repository.require_conversation_actor(
                request.conversation_id,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
            )
        trace_id = f"trace_{uuid4().hex}"
        await self.repository.create_trace(
            TraceRecord(
                trace_id=trace_id,
                conversation_id=conversation.id,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
            )
        )
        await self.repository.close_dangling_tool_calls(conversation.id, trace_id=trace_id)
        await self.repository.append_provider_messages(
            conversation.id,
            [{"role": "user", "content": request.message}],
            trace_id=trace_id,
        )
        await self.repository.add_trace_event(
            trace_id,
            TraceEvent(kind="user.request", status="completed", details={"content_length": len(request.message)}),
        )
        conversation = await self.repository.get_conversation(conversation.id)
        return await self._run(
            conversation=conversation,
            trace_id=trace_id,
            forced_capability=request.capability,
            event_sink=event_sink,
        )

    async def confirm(self, approval_id: str, *, user_id: str, tenant_id: str) -> ChatResponse:
        approval = await self.repository.get_approval(approval_id)
        if approval.user_id != user_id or approval.tenant_id != tenant_id:
            raise PlatformError("PERMISSION_DENIED", "Approval ownership does not match the caller", status_code=403)
        if approval.status != "pending":
            raise PlatformError("CONFLICT", f"Approval is already {approval.status}", status_code=409)
        conversation = await self.repository.require_conversation_actor(
            approval.conversation_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        if not conversation.messages or not conversation.messages[-1].tool_calls:
            raise PlatformError("CONFLICT", "The approval no longer has a pending tool call", status_code=409)
        await self.repository.set_approval_status(approval_id, "approved")
        await self.repository.add_trace_event(
            approval.trace_id,
            TraceEvent(
                kind="approval.confirmed",
                status="completed",
                details={"approval_id": approval_id},
            ),
        )
        response = await self._run(
            conversation=conversation,
            trace_id=approval.trace_id,
            approved_call_ids={str(call.get("id")) for call in approval.tool_calls},
            resume=True,
        )
        await self.repository.set_approval_status(approval_id, "executed")
        return response

    async def deny(self, approval_id: str, *, user_id: str, tenant_id: str) -> ApprovalRecord:
        approval = await self.repository.get_approval(approval_id)
        if approval.user_id != user_id or approval.tenant_id != tenant_id:
            raise PlatformError("PERMISSION_DENIED", "Approval ownership does not match the caller", status_code=403)
        if approval.status != "pending":
            raise PlatformError("CONFLICT", f"Approval is already {approval.status}", status_code=409)
        denied = await self.repository.set_approval_status(approval_id, "denied")
        closing = [
            {
                "role": "tool",
                "name": (call.get("function") or {}).get("name", "unknown"),
                "tool_call_id": call.get("id"),
                "content": "The user denied this operation.",
            }
            for call in approval.tool_calls
        ]
        closing.append({"role": "assistant", "content": "The requested operation was cancelled."})
        await self.repository.append_provider_messages(approval.conversation_id, closing, trace_id=approval.trace_id)
        await self.repository.add_trace_event(
            approval.trace_id,
            TraceEvent(kind="approval.denied", status="completed", details={"approval_id": approval_id}),
        )
        await self.repository.finish_trace(approval.trace_id, "completed")
        return denied

    async def _run(
        self,
        *,
        conversation: Conversation,
        trace_id: str,
        forced_capability: str | None = None,
        approved_call_ids: set[str] | None = None,
        resume: bool = False,
        event_sink: EventSink | None = None,
    ) -> ChatResponse:
        capabilities = await self._available_capabilities(conversation)
        forced_function = self._resolve_forced_capability(forced_capability, capabilities)
        await self.repository.add_trace_event(
            trace_id,
            TraceEvent(
                kind="capability.discovery",
                status="completed",
                details={
                    "tool_count": sum(item.kind == "tool" for item in capabilities.values()),
                    "aina_count": sum(item.kind == "aina" for item in capabilities.values()),
                    "forced": forced_capability,
                },
            ),
        )
        system_prompt = await self._system_prompt()
        messages = [
            {"role": "system", "content": system_prompt},
            *[message.provider_message() for message in conversation.messages],
        ]
        persist_from = len(messages)
        state: AgentState = {
            "messages": messages,
            "capabilities": capabilities,
            "tool_definitions": [item.llm_definition() for item in capabilities.values()],
            "trace_id": trace_id,
            "conversation_id": conversation.id,
            "user_id": conversation.user_id,
            "tenant_id": conversation.tenant_id,
            "iterations": 0,
            "max_iterations": self.settings.max_agent_iterations,
            "forced_function": forced_function,
            "approved_call_ids": approved_call_ids or set(),
            "resume": resume,
            "event_sink": event_sink,
            "usage_input": 0,
            "usage_output": 0,
            "call_counts": {},
            "approval": None,
        }
        try:
            result = await self._graph.ainvoke(state)
        except PlatformError:
            await self.repository.finish_trace(trace_id, "failed")
            raise
        new_messages = result["messages"][persist_from:]
        appended = await self.repository.append_provider_messages(
            conversation.id,
            new_messages,
            trace_id=trace_id,
        )
        status = result.get("final_status", "failed")
        await self.repository.add_trace_event(
            trace_id,
            TraceEvent(kind="final.response", status=status, details={"iterations": result.get("iterations", 0)}),
        )
        await self.repository.finish_trace(trace_id, status)
        last_assistant = next((item for item in reversed(appended) if item.role == "assistant"), None)
        return ChatResponse(
            conversation_id=conversation.id,
            message_id=last_assistant.id if last_assistant else None,
            content=result.get("final_content", "The agent stopped without a final response."),
            status=status,
            trace_id=trace_id,
            iterations=result.get("iterations", 0),
            usage=Usage(
                input_tokens=result.get("usage_input", 0),
                output_tokens=result.get("usage_output", 0),
            ),
            approval=result.get("approval"),
        )

    async def _system_prompt(self) -> str:
        skills = [item for item in await self.repository.list_skills() if item.status == "published"]
        if not skills:
            return self.settings.system_prompt
        guidance = "\n".join(
            f"- {skill.name}: {skill.instructions} (related tools: {', '.join(skill.tools) or 'none'})"
            for skill in skills
        )
        return f"{self.settings.system_prompt}\n\nAvailable platform skills:\n{guidance}"

    async def _available_capabilities(self, conversation: Conversation) -> dict[str, Capability]:
        capabilities: dict[str, Capability] = {}
        for tool in await self.repository.list_tools():
            if tool.status != "published":
                continue
            function_name = _function_name("tool", tool.tool_id)
            capabilities[function_name] = Capability(
                kind="tool",
                capability_id=tool.tool_id,
                function_name=function_name,
                display_name=tool.name,
                description=tool.description,
                input_schema=tool.input_schema,
                requires_confirmation=tool.side_effect_level == "high",
                value=tool,
            )
        installations = await self.repository.list_installations(
            tenant_id=conversation.tenant_id,
            user_id=conversation.user_id,
        )
        for installation in installations:
            if installation.status != "active":
                continue
            if conversation.enabled_ainas and installation.aina_id not in conversation.enabled_ainas:
                continue
            try:
                aina = await self.repository.get_aina(installation.aina_id)
            except PlatformError:
                continue
            if aina.status != "registered":
                continue
            missing = set(aina.manifest.permissions) - set(installation.granted_permissions)
            if missing:
                continue
            function_name = _function_name("aina", installation.aina_id)
            capability_descriptions = [
                item.description for item in [*aina.manifest.capabilities.skills, *aina.manifest.capabilities.tools]
            ]
            description = aina.manifest.aina.description
            if capability_descriptions:
                description = f"{description}. Capabilities: {'; '.join(capability_descriptions)}"
            capabilities[function_name] = Capability(
                kind="aina",
                capability_id=installation.aina_id,
                function_name=function_name,
                display_name=aina.manifest.aina.name,
                description=description,
                input_schema={
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": "The user request or task for the AINA.",
                        }
                    },
                    "required": ["input"],
                    "additionalProperties": True,
                },
                requires_confirmation=_permissions_are_high_risk(aina.manifest.permissions),
                value=(aina, installation),
            )
        return capabilities

    @staticmethod
    def _resolve_forced_capability(
        requested: str | None,
        capabilities: dict[str, Capability],
    ) -> str | None:
        if requested is None:
            return None
        kind: str | None = None
        capability_id = requested
        if ":" in requested:
            kind, capability_id = requested.split(":", 1)
            if kind not in {"tool", "aina"}:
                raise PlatformError("INVALID_REQUEST", "Capability must use tool:<id> or aina:<id>")
        matches = [
            item
            for item in capabilities.values()
            if item.capability_id == capability_id and (kind is None or item.kind == kind)
        ]
        if len(matches) != 1:
            raise PlatformError(
                "PERMISSION_DENIED",
                "The requested capability is not installed, enabled, or fully authorized",
                status_code=403,
            )
        return matches[0].function_name


def _function_name(kind: str, capability_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", capability_id).strip("_") or "capability"
    digest = hashlib.sha1(f"{kind}:{capability_id}".encode()).hexdigest()[:8]
    prefix = f"{kind}_"
    available = 64 - len(prefix) - len(digest) - 1
    return f"{prefix}{safe[:available]}_{digest}"


def _permissions_are_high_risk(permissions: list[str]) -> bool:
    return any(marker in permission.lower() for permission in permissions for marker in _HIGH_RISK_MARKERS)
