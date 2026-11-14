"""Observation domain models (traces, spans, LLM call records)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from tianzhou_agent_platform.core.base import StrictModel, utc_now
from unibot_agent.types import ModelCallRecord


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
    input: Any | None = None
    output: Any | None = None
    attributes: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None


class TraceRecord(StrictModel):
    trace_id: str
    root_span_id: str | None = None
    conversation_id: str | None = None
    user_id: str
    tenant_id: str
    status: Literal["running", "completed", "approval_required", "failed", "cancelled"] = "running"
    events: list[TraceEvent] = Field(default_factory=list)
    spans: list[TraceSpan] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


# The agent core writes this record and owns its shape.
LLMCallRecord = ModelCallRecord
