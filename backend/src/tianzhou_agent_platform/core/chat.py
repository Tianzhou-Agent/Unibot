from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.core.base import StrictModel, Usage, utc_now


class ChatRequest(StrictModel):
    message: str = Field(min_length=1)
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


class TraceRecord(StrictModel):
    trace_id: str
    conversation_id: str | None = None
    user_id: str
    tenant_id: str
    status: Literal["running", "completed", "approval_required", "failed"] = "running"
    events: list[TraceEvent] = Field(default_factory=list)
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
