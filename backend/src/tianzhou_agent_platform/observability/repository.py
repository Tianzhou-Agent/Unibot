"""Persistence contract of traces and LLM call records kept by the repository (the legacy trace path)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from tianzhou_agent_platform.observability.models import LLMCallRecord, TraceEvent, TraceRecord, TraceSpan


@runtime_checkable
class TraceRepository(Protocol):
    async def create_trace(self, trace: TraceRecord) -> TraceRecord: ...

    async def add_trace_event(self, trace_id: str, event: TraceEvent) -> None: ...

    async def add_trace_span(self, trace_id: str, span: TraceSpan) -> TraceSpan: ...

    async def ensure_trace_root_span(self, trace_id: str, span: TraceSpan) -> TraceSpan: ...

    async def finish_trace_span(
        self,
        trace_id: str,
        span_id: str,
        status: str,
        *,
        input_data: Any | None = None,
        output_data: Any | None = None,
        attributes: dict[str, Any] | None = None,
        first_output_at: datetime | None = None,
        error: dict[str, Any] | None = None,
    ) -> TraceSpan: ...

    async def finish_trace(self, trace_id: str, status: str) -> TraceRecord: ...

    async def get_trace(self, trace_id: str) -> TraceRecord: ...

    async def list_traces(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[TraceRecord]: ...

    async def upsert_llm_call(self, call: LLMCallRecord) -> None: ...

    async def list_llm_calls(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        trace_ids: set[str] | None = None,
        context_ids: set[str] | None = None,
    ) -> list[LLMCallRecord]: ...

    async def count_llm_calls(
        self,
        *,
        trace_ids: set[str] | None = None,
        context_ids: set[str] | None = None,
    ) -> int: ...
