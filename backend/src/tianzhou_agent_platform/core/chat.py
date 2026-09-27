"""Compatibility re-export. Import from owning feature packages instead.

Public chat/approval schemas live in conversations.schemas.
Trace/LLM call records live in observability.models.
Error envelopes live in core.errors.

Temporary shim with removal gate: Phase 7 after all callers migrate.
"""

from tianzhou_agent_platform.conversations.schemas import (
    ApprovalAction,
    ApprovalRecord,
    ChatRequest,
    ChatResponse,
)
from tianzhou_agent_platform.observability.models import (
    LLMCallRecord,
    TraceEvent,
    TraceRecord,
    TraceSpan,
)

# Error value schemas remain shared; re-exported here for the historical barrel.
from tianzhou_agent_platform.core.errors import ErrorEnvelope, StandardError  # noqa: F401

__all__ = [
    "ApprovalAction",
    "ApprovalRecord",
    "ChatRequest",
    "ChatResponse",
    "ErrorEnvelope",
    "LLMCallRecord",
    "StandardError",
    "TraceEvent",
    "TraceRecord",
    "TraceSpan",
]
