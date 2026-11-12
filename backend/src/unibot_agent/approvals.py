"""Native human-in-the-loop for risky capabilities and its public approval mapping.

``HumanInTheLoopMiddleware`` pauses the whole model response (no sibling call runs) with a checkpointed interrupt
and resumes it with ``Command(resume=...)``. This module configures it, maps the interrupt to the public
``ApprovalRequest`` and supplies the texts of the runtime's ``TerminalDenialMiddleware``: a denied batch closes every
pending call and ends the run without another model answer.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any
from uuid import uuid4

from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Interrupt

from unibot_agent.errors import AgentError
from unibot_agent.capabilities import Capability
from unibot_agent.capability_tools import validate_capability_args
from unibot_agent.history import native_to_wire
from unibot_agent.types import ApprovalRequest

DENIED_TOOL_RESULT = "The user denied this operation."
CANCELLED_REPLY = "The requested operation was cancelled."


def build_approval_middleware(registry: dict[str, Capability]) -> HumanInTheLoopMiddleware:
    """Interrupt before any call of a capability that requires confirmation.

    Invalid arguments never ask for approval: such a call runs (and fails validation) without an interrupt.
    """
    return HumanInTheLoopMiddleware(
        interrupt_on={
            name: InterruptOnConfig(
                # ``edit`` carries arguments the user changed when approving, validated by ``validated_edits``.
                allowed_decisions=(
                    ["approve", "reject", "edit"] if capability.editable else ["approve", "reject"]
                ),
                description=capability.display_name,
                when=_has_valid_arguments(capability),
            )
            for name, capability in registry.items()
            if capability.requires_confirmation
        },
    )


def _has_valid_arguments(capability: Capability) -> Any:
    def when(request: Any) -> bool:
        try:
            validate_capability_args(
                input_schema=capability.input_schema,
                args=request.tool_call.get("args"),
                function_name=capability.function_name,
            )
        except AgentError:
            return False
        if capability.confirm_once and _completed_before(request.state, capability.function_name):
            return False
        return True

    return when


def _completed_before(state: Any, function_name: str) -> bool:
    """Whether a call of this capability already completed (so was approved) earlier in the conversation."""
    messages = state.get("messages", []) if isinstance(state, dict) else getattr(state, "messages", [])
    return any(
        isinstance(message, ToolMessage)
        and message.name == function_name
        and message.status != "error"
        and message.content != DENIED_TOOL_RESULT
        for message in messages
    )


def approval_request(
    *,
    interrupt: Interrupt,
    messages: list[Any],
    conversation_id: str,
    user_id: str,
    tenant_id: str,
    trace_id: str,
    thread_id: str,
) -> ApprovalRequest:
    """Public approval for a paused batch; the native interrupt is kept as an opaque execution reference."""
    batch = next((message for message in reversed(messages) if isinstance(message, AIMessage)), None)
    wire = native_to_wire(batch) if batch is not None else None
    actions = interrupt.value.get("action_requests") or []
    return ApprovalRequest(
        id=f"approval_{uuid4().hex}",
        conversation_id=conversation_id,
        user_id=user_id,
        tenant_id=tenant_id,
        trace_id=trace_id,
        tool_calls=(wire or {}).get("tool_calls") or [],
        capability_names=[str(action.get("description") or action.get("name")) for action in actions],
        runtime_ref={
            "engine": "langgraph",
            "thread_id": thread_id,
            "interrupt_id": interrupt.id,
            "action_count": len(actions),
        },
    )


def resume_decisions(
    interrupt: Interrupt,
    *,
    approve: bool,
    approval: ApprovalRequest | None = None,
    edits: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """``Command(resume=...)`` payload deciding every action of the paused batch the same way.

    An approved action whose call has validated ``edits`` runs with the edited arguments instead.
    """
    actions = interrupt.value.get("action_requests") or []
    calls = interrupted_calls(interrupt, approval.tool_calls) if approval is not None else [None] * len(actions)
    decisions: list[dict[str, Any]] = []
    for action, call in zip(actions, calls, strict=True):
        call_id = str((call or {}).get("id") or "")
        if approve and edits and call_id in edits:
            decisions.append({"type": "edit", "edited_action": {"name": action["name"], "args": edits[call_id]}})
        else:
            decisions.append({"type": "approve" if approve else "reject"})
    return {"decisions": decisions}


def interrupted_calls(interrupt: Interrupt, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
    """The paused batch's wire tool calls, in the order of the interrupt's action requests."""
    remaining = list(tool_calls)
    matched: list[dict[str, Any] | None] = []
    for action in interrupt.value.get("action_requests") or []:
        found = next(
            (
                call
                for call in remaining
                if (call.get("function") or {}).get("name") == action.get("name")
                and _arguments(call) == action.get("args")
            ),
            None,
        )
        if found is not None:
            remaining.remove(found)
        matched.append(found)
    return matched


def with_confirmations(
    approval: ApprovalRequest, interrupt: Interrupt, registry: dict[str, Capability]
) -> ApprovalRequest:
    """The approval with what each paused call shows the user when asking (``Capability.confirmation``)."""
    confirmations = []
    editable = set()
    for call in interrupted_calls(interrupt, approval.tool_calls):
        if call is None:
            continue
        capability = registry.get(str((call.get("function") or {}).get("name") or ""))
        if capability is None:
            continue
        shown = capability.confirmation(str(call.get("id")), _arguments(call))
        if shown is not None:
            confirmations.append(shown)
            editable.add(str(call.get("id")))
    return replace(approval, confirmations=confirmations, editable_call_ids=frozenset(editable))


def validated_edits(
    approval: ApprovalRequest,
    edits: dict[str, dict[str, Any]],
    registry: dict[str, Capability],
) -> dict[str, dict[str, Any]]:
    """Proposed arguments, admitted only for calls that showed a confirmation and only when they validate."""
    calls = {str(call.get("id")): call for call in approval.tool_calls}
    for call_id, arguments in edits.items():
        call = calls.get(call_id)
        if call_id not in approval.editable_call_ids or call is None:
            raise AgentError(
                "INVALID_REQUEST",
                f"Tool call {call_id!r} cannot be edited",
                status_code=422,
            )
        name = str((call.get("function") or {}).get("name") or "")
        capability = registry.get(name)
        if capability is None:
            raise AgentError("INVALID_REQUEST", f"Tool call {call_id!r} cannot be edited", status_code=422)
        validate_capability_args(input_schema=capability.input_schema, args=arguments, function_name=name)
        capability.check_edit(arguments)
    return edits


def _arguments(call: dict[str, Any]) -> dict[str, Any]:
    raw = (call.get("function") or {}).get("arguments")
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
