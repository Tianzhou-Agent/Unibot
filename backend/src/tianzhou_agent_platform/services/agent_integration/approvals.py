"""Native human-in-the-loop for risky capabilities and its public approval mapping.

``HumanInTheLoopMiddleware`` pauses the whole model response (no sibling call runs) with a checkpointed interrupt
and resumes it with ``Command(resume=...)``. This module configures it, maps the interrupt to the public
``ApprovalRecord`` and supplies the texts of the runtime's ``TerminalDenialMiddleware``: a denied batch closes every
pending call and ends the run without another model answer.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig
from langchain_core.messages import AIMessage
from langgraph.types import Interrupt

from tianzhou_agent_platform.conversations.schemas import ApprovalRecord
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.services.agent_integration.capabilities import Capability
from tianzhou_agent_platform.services.agent_integration.capability_tools import validate_capability_args
from tianzhou_agent_platform.services.agent_integration.history import native_to_wire

DENIED_TOOL_RESULT = "The user denied this operation."
CANCELLED_REPLY = "The requested operation was cancelled."


def build_approval_middleware(registry: dict[str, Capability]) -> HumanInTheLoopMiddleware:
    """Interrupt before any call of a capability that requires confirmation.

    Invalid arguments never ask for approval: such a call runs (and fails validation) without an interrupt.
    """
    return HumanInTheLoopMiddleware(
        interrupt_on={
            name: InterruptOnConfig(
                allowed_decisions=["approve", "reject"],
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
        except PlatformError:
            return False
        return True

    return when


def approval_record(
    *,
    interrupt: Interrupt,
    messages: list[Any],
    conversation_id: str,
    user_id: str,
    tenant_id: str,
    trace_id: str,
    thread_id: str,
) -> ApprovalRecord:
    """Public approval for a paused batch; the native interrupt is kept as an opaque execution reference."""
    batch = next((message for message in reversed(messages) if isinstance(message, AIMessage)), None)
    wire = native_to_wire(batch) if batch is not None else None
    actions = interrupt.value.get("action_requests") or []
    return ApprovalRecord(
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


def resume_decisions(interrupt: Interrupt, *, approve: bool) -> dict[str, Any]:
    """``Command(resume=...)`` payload deciding every action of the paused batch the same way."""
    count = len(interrupt.value.get("action_requests") or [])
    return {"decisions": [{"type": "approve" if approve else "reject"} for _ in range(count)]}
