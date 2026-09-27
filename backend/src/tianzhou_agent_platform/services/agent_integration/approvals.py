"""Native HITL configuration and public approval mapping (plan §6.6).

HumanInTheLoopMiddleware owns interruption/resumption. This module maps
native decisions/interrupts to application execution references and configures
batch/terminal policy. ConversationService owns approval records.
"""

from __future__ import annotations

from typing import Any, Iterable, Literal, Sequence
from uuid import uuid4

from langchain.agents.middleware import HumanInTheLoopMiddleware, InterruptOnConfig

from tianzhou_agent_platform.conversations.schemas import ApprovalRecord

DecisionType = Literal["approve", "edit", "reject", "respond"]


def build_hitl_middleware(
    *,
    risky_tools: dict[str, Sequence[DecisionType] | bool],
    description_prefix: str = "Tool execution requires approval",
) -> HumanInTheLoopMiddleware:
    """Configure native HITL for risky tools.

    Invalid risky arguments must produce errors, not approval prompts — the
    ``when`` predicate and pre-validation in ChatService handle that.
    """
    interrupt_on: dict[str, Any] = {}
    for name, config in risky_tools.items():
        if config is True:
            interrupt_on[name] = InterruptOnConfig(
                allowed_decisions=["approve", "edit", "reject", "respond"]
            )
        elif config is False:
            continue
        else:
            interrupt_on[name] = InterruptOnConfig(allowed_decisions=list(config))
    return HumanInTheLoopMiddleware(
        interrupt_on=interrupt_on,
        description_prefix=description_prefix,
    )


def native_resume_payload(decisions: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Public confirm/deny → native Command(resume=...) payload.

    HITL resume shape: ``{"decisions": [{"type": "approve"|...}, ...]}``.
    """
    return {"decisions": [dict(item) for item in decisions]}


def approve_all(count: int) -> dict[str, Any]:
    return native_resume_payload([{"type": "approve"}] * count)


def reject_all(count: int) -> dict[str, Any]:
    """Denial closes pending calls without another model generation."""
    return native_resume_payload([{"type": "reject"}] * count)


def approval_record_from_interrupt(
    *,
    conversation_id: str,
    user_id: str,
    tenant_id: str,
    trace_id: str,
    tool_calls: list[dict[str, Any]],
    capability_names: list[str],
    runtime_ref: dict[str, Any],
    run_generation: int = 0,
) -> ApprovalRecord:
    """Durably associate approval ID with runtime/thread/interrupt metadata."""
    return ApprovalRecord(
        id=f"approval_{uuid4().hex}",
        conversation_id=conversation_id,
        user_id=user_id,
        tenant_id=tenant_id,
        trace_id=trace_id,
        tool_calls=tool_calls,
        capability_names=capability_names,
        status="pending",
        runtime_ref=runtime_ref,
        run_generation=run_generation,
    )


def is_interrupt_payload(value: Any) -> bool:
    return isinstance(value, dict) and ("decisions" in value or "__interrupt__" in str(value))
