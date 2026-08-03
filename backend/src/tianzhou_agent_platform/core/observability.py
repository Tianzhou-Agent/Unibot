from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import wraps
from typing import Any, Literal, ParamSpec, TypeVar, cast

from tianzhou_agent_platform.core.chat import ApprovalRecord, LLMCallRecord, TraceEvent, TraceRecord, TraceSpan
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.core.trace_details import sanitize_trace_data

logger = logging.getLogger(__name__)

P = ParamSpec("P")
R = TypeVar("R")


def observation_slice(operation: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R | None]]:
    """Keep observability failures outside the business control flow."""

    @wraps(operation)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R | None:
        try:
            return await operation(*args, **kwargs)
        except Exception:
            logger.exception("Observability operation %s failed", operation.__name__)
            return None

    return wrapped


class ObservabilityAspect:
    """Non-blocking Trace/Span/Event persistence boundary."""

    def __init__(self, repository: InMemoryRepository) -> None:
        self._repository = repository

    @observation_slice
    async def create_agent_trace(
        self,
        *,
        trace_id: str,
        root_span_id: str,
        conversation_id: str,
        user_id: str,
        tenant_id: str,
        input_data: Any,
        attributes: dict[str, Any],
    ) -> bool:
        await self._repository.create_trace(
            TraceRecord(
                trace_id=trace_id,
                root_span_id=root_span_id,
                conversation_id=conversation_id,
                user_id=user_id,
                tenant_id=tenant_id,
                spans=[
                    TraceSpan(
                        span_id=root_span_id,
                        kind="agent",
                        name="agent.run",
                        target_id="unibot",
                        input=sanitize_trace_data(input_data),
                        attributes=cast(dict[str, Any], sanitize_trace_data(attributes)),
                    )
                ],
            )
        )
        return True

    @observation_slice
    async def record_event(
        self,
        trace_id: str,
        *,
        kind: str,
        status: Literal["started", "completed", "failed", "pending"],
        target_type: str | None = None,
        target_id: str | None = None,
        duration_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        await self._repository.add_trace_event(
            trace_id,
            TraceEvent(
                kind=kind,
                status=status,
                target_type=target_type,
                target_id=target_id,
                duration_ms=duration_ms,
                details=cast(dict[str, Any], sanitize_trace_data(details or {})),
            ),
        )

    @observation_slice
    async def record_llm_call(self, call: LLMCallRecord) -> None:
        sanitized = call.model_copy(
            update={
                "request": sanitize_trace_data(call.request),
                "response": sanitize_trace_data(call.response),
                "error": sanitize_trace_data(call.error),
            }
        )
        await self._repository.upsert_llm_call(sanitized)

    @observation_slice
    async def start_span(
        self,
        trace_id: str,
        *,
        span_id: str,
        parent_span_id: str,
        kind: Literal["agent", "model", "tool", "aina", "internal"],
        name: str,
        target_id: str | None = None,
        target_version: str | None = None,
        logical_call_id: str | None = None,
        input_data: Any | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        await self._repository.add_trace_span(
            trace_id,
            TraceSpan(
                span_id=span_id,
                parent_span_id=parent_span_id,
                kind=kind,
                name=name,
                target_id=target_id,
                target_version=target_version,
                logical_call_id=logical_call_id,
                input=sanitize_trace_data(input_data),
                attributes=cast(dict[str, Any], sanitize_trace_data(attributes or {})),
            ),
        )

    @observation_slice
    async def finish_span(
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
    ) -> None:
        await self._repository.finish_trace_span(
            trace_id,
            span_id,
            status,
            input_data=sanitize_trace_data(input_data),
            output_data=sanitize_trace_data(output_data),
            attributes=(
                cast(dict[str, Any], sanitize_trace_data(attributes))
                if attributes is not None
                else None
            ),
            first_output_at=first_output_at,
            error=cast(dict[str, Any], sanitize_trace_data(error)) if error is not None else None,
        )

    @observation_slice
    async def finish_trace(self, trace_id: str, status: str) -> None:
        await self._repository.finish_trace(trace_id, status)

    async def record_cancelled_approvals(self, approvals: list[ApprovalRecord]) -> None:
        for approval in approvals:
            await self.record_event(
                approval.trace_id,
                kind="approval.cancelled",
                status="completed",
                details={"approval_id": approval.id},
            )
            await self.finish_trace(approval.trace_id, "completed")

    async def ensure_agent_root_span(self, trace_id: str, *, span_id: str, conversation_id: str) -> str:
        fallback = TraceSpan(
            span_id=span_id,
            kind="agent",
            name="agent.run",
            target_id="unibot",
            attributes={"conversation_id": conversation_id, "migrated_trace": True},
        )
        try:
            trace = await self._repository.get_trace(trace_id)
            if trace.root_span_id is not None:
                return trace.root_span_id
            span = fallback.model_copy(update={"started_at": trace.created_at})
            root = await self._repository.ensure_trace_root_span(trace_id, span)
            return root.span_id
        except Exception:
            logger.exception("Observability operation ensure_root_span failed")
            return fallback.span_id
