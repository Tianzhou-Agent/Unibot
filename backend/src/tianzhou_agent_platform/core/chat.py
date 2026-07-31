from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.core.base import StrictModel, Usage, utc_now


class ChatRequest(StrictModel):
    message: str = Field(min_length=1)
    ui_context: str | None = Field(
        default=None,
        max_length=4_000,
        description="Transient UI context for the current request; it is not persisted as conversation content.",
    )
    conversation_id: str | None = None
    user_id: str = "anonymous"
    tenant_id: str = "default"
    capability: str | None = Field(
        default=None,
        description="Optionally force the first call to tool:<id> or aina:<id>.",
    )
    preferred_aina_id: str | None = Field(
        default=None,
        description="Prefer an AINA without forcing every capability call to remain in that application.",
    )


class ApprovalAction(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"


class ApprovalRecord(StrictModel):
    id: str
    conversation_id: str
    user_id: str
    tenant_id: str
    trace_id: str
    tool_calls: list[dict[str, Any]]
    capability_names: list[str]
    status: Literal["pending", "approved", "denied", "executed"] = "pending"
    created_at: datetime = Field(default_factory=utc_now)
    resolved_at: datetime | None = None


class ChatResponse(StrictModel):
    conversation_id: str
    message_id: str | None = None
    content: str
    status: Literal["completed", "approval_required", "failed"]
    trace_id: str
    iterations: int
    usage: Usage = Field(default_factory=Usage)
    approval: ApprovalRecord | None = None
    widgets: list[WidgetDefinition] = Field(default_factory=list)


class TraceEvent(StrictModel):
    timestamp: datetime = Field(default_factory=utc_now)
    kind: str
    status: str
    target_type: str | None = None
    target_id: str | None = None
    duration_ms: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class TraceSpan(StrictModel):
    span_id: str
    parent_span_id: str | None = None
    kind: Literal["agent", "model", "tool", "aina", "internal"]
    name: str
    status: Literal["running", "completed", "failed", "cancelled", "approval_required"] = "running"
    target_id: str | None = None
    target_version: str | None = None
    logical_call_id: str | None = None
    attempt_no: int = Field(default=1, ge=1)
    started_at: datetime = Field(default_factory=utc_now)
    first_output_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class TraceRecord(StrictModel):
    trace_id: str
    root_span_id: str | None = None
    conversation_id: str | None = None
    user_id: str
    tenant_id: str
    status: Literal["running", "completed", "approval_required", "failed"] = "running"
    events: list[TraceEvent] = Field(default_factory=list)
    spans: list[TraceSpan] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class LLMCallRecord(StrictModel):
    call_id: str
    trace_id: str | None = None
    span_id: str | None = None
    context_type: str | None = None
    context_id: str | None = None
    endpoint: str
    model: str
    status: Literal["running", "completed", "failed"] = "running"
    request: dict[str, Any]
    response: dict[str, Any] | None = None
    duration_ms: float | None = None
    first_token_at: datetime | None = None
    ttft_ms: float | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class StandardError(StrictModel):
    code: str
    message: str
    retryable: bool
    source: str
    user_message: str
    trace_id: str


class ErrorEnvelope(StrictModel):
    error: StandardError
