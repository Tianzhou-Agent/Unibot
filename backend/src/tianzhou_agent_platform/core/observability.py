"""Observability boundary: legacy repository persistence plus the OTel + WAL
dual-write pipeline (design section 17.2, phase two of the migration).

Every public method keeps its original signature and still writes the legacy
repository path. When the OBS pipeline is enabled (a ``WalWriter``, tracer and
RawIoWriter are injected), the same business event also flows through OTel
spans into the WAL:

- ``create_agent_trace`` -> OTel root span + ``trace_started`` record
- ``start_span`` -> child OTel span + ``span_started`` record
- ``finish_span`` -> span attributes/preview/error/raw IO + ``span_finished``
- ``finish_trace`` -> root span end + complete ``trace_finished`` record +
  WAL fsync barrier (records durable before the business returns)
- ``record_event`` -> ``event`` record
- ``record_llm_call`` -> staged as the raw-IO source of the matching Model
  Span (the LLM call itself keeps using the legacy repository this phase)

All new-path failures are swallowed: the legacy path is the migration
baseline and the observation slice contract stays intact.
"""

from __future__ import annotations

import json
import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Literal, ParamSpec, TypeVar, cast

from opentelemetry import trace as otel_trace
from opentelemetry.trace import Span

from tianzhou_agent_platform.core.chat import ApprovalRecord, LLMCallRecord, TraceEvent, TraceRecord, TraceSpan
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.core.telemetry import (
    ATTR_CONVERSATION_ID,
    ATTR_ERROR_JSON,
    ATTR_FIRST_OUTPUT_AT,
    ATTR_INPUT_PREVIEW,
    ATTR_INPUT_TOKENS,
    ATTR_LEGACY_PARENT_SPAN_ID,
    ATTR_LEGACY_SPAN_ID,
    ATTR_LEGACY_TRACE_ID,
    ATTR_OUTPUT_PREVIEW,
    ATTR_OUTPUT_TOKENS,
    ATTR_RAW_IO_PATH,
    ATTR_RAW_IO_SHA256,
    ATTR_RAW_IO_SIZE,
    ATTR_RAW_IO_STATUS,
    ATTR_SEQUENCE_NO,
    ATTR_SESSION_ID,
    ATTR_SPAN_KIND,
    ATTR_SPAN_ROLE,
    ATTR_TARGET_ID,
    ATTR_TENANT_ID,
    ATTR_TRACE_STATUS,
    ATTR_TTFT_MS,
    ATTR_USER_ID,
    STATUS_APPROVAL_REQUIRED,
    STATUS_RUNNING,
    UNIBOT_STATUS_ATTR,
)
from tianzhou_agent_platform.core.trace_details import summarize_trace_data
from tianzhou_agent_platform.store.observability_raw import RawIoRef, RawIoWriter
from tianzhou_agent_platform.store.observability_wal import ObsRecord, WalError, WalGapError, WalWriter

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


@dataclass(slots=True)
class TraceContext:
    conversation_id: str | None
    user_id: str
    tenant_id: str


def _otel_trace_id_for(legacy_trace_id: str) -> str:
    """``trace_<32hex>`` -> ``<32hex>``; anything else is assumed OTel format."""
    if legacy_trace_id.startswith("trace_") and len(legacy_trace_id) == 38:
        return legacy_trace_id[6:]
    return legacy_trace_id


def _otel_span_id_for(legacy_span_id: str) -> str:
    if legacy_span_id.startswith("span_") and len(legacy_span_id) == 21:
        return legacy_span_id[5:]
    return legacy_span_id


def _derived_span_id(legacy_span_id: str) -> str:
    """Deterministic 16-hex span id for the direct-write fallback when the
    live legacy->otel mapping is unavailable: legacy ids are 37 chars and
    overflow VARCHAR(32) (review round 3, P1)."""
    import hashlib

    return hashlib.sha256(legacy_span_id.encode("utf-8")).hexdigest()[:16]


def _root_span_context(otel_trace_id: str):
    """A non-recording parent context pinning the root span's trace_id so WAL
    trace records and span records share the same trace identity."""
    from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState

    if len(otel_trace_id) != 32:
        return None
    try:
        trace_int = int(otel_trace_id, 16)
    except ValueError:
        return None
    parent = NonRecordingSpan(
        SpanContext(
            trace_id=trace_int,
            span_id=1,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
            trace_state=TraceState(),
        )
    )
    return otel_trace.set_span_in_context(parent)


def _preview_json(value: Any) -> str | None:
    if value is None:
        return None
    preview = summarize_trace_data(value)
    return json.dumps(preview, ensure_ascii=False, default=str)


class ObservabilityAspect:
    """Non-blocking Trace/Span/Event persistence boundary (legacy + OTel/WAL)."""

    def __init__(
        self,
        repository: InMemoryRepository,
        *,
        wal_writer: WalWriter | None = None,
        tracer: Any | None = None,
        raw_io_writer: RawIoWriter | None = None,
        obs_store: Any | None = None,
    ) -> None:
        self._repository = repository
        self._wal_writer = wal_writer
        self._tracer = tracer
        self._raw_io_writer = raw_io_writer
        # direct-write fallback when the WAL is unavailable but MySQL is
        # healthy (design section 14)
        self._obs_store = obs_store
        self._enabled = wal_writer is not None and tracer is not None
        self._spans: dict[str, Span] = {}  # legacy span_id -> OTel span
        self._otel_trace_ids: dict[str, str] = {}  # legacy trace_id -> OTel trace_id
        self._otel_root_span_ids: dict[str, str] = {}  # legacy trace_id -> OTel root span_id
        # legacy span_id -> OTel span id (16 hex): the direct-write fallback
        # needs real OTel span ids because legacy ids exceed VARCHAR(32)
        # (review round 3, P1). Grouped by trace so terminal cleanup can drop
        # the whole trace's mappings (review round 4, P2).
        self._otel_span_ids: dict[str, str] = {}
        self._span_ids_by_trace: dict[str, set[str]] = {}
        self._trace_contexts: dict[str, TraceContext] = {}
        self._trace_seq: dict[str, int] = {}  # OTel trace_id -> next span sequence
        self._pending_llm_calls: dict[str, list[LLMCallRecord]] = {}  # span_id -> attempts
        # legacy trace_id -> (input, output, cache) conversation totals set on
        # the root span by finish_span (review P1-4)
        self._trace_token_totals: dict[str, tuple[int, int, int]] = {}
        # serializes trace-context recovery so concurrent resumes cannot
        # rebuild the same continuation root span twice (review round 3)
        self._restore_lock = asyncio.Lock()

    async def _ensure_trace_context(
        self,
        trace_id: str,
        legacy_root_span_id: str | None = None,
        conversation_id: str | None = None,
    ) -> bool:
        """Recover trace context for cross-instance or restart approval
        resumes (review round 3/4, P1). Sources, in order:

        1. OBS pipeline (canonical id + user/tenant/session) — eventually
           consistent, may be empty right after an approval;
        2. business data: the conversation record (persisted) — used when
           the OBS row is not visible yet;
        3. the legacy id itself derives the canonical OTel trace id
           (``trace_<32hex>`` -> ``<32hex>``).

        A continuation root span is rebuilt so new spans keep the same trace
        identity.
        """
        if trace_id in self._otel_trace_ids:
            # mapping already recovered (e.g. by an earlier record_event);
            # enrich an anonymous context once the resume path supplies its
            # conversation id, then ensure the caller's root handle exists.
            refreshed_context: TraceContext | None = None
            existing_context = self._trace_contexts.get(trace_id)
            if conversation_id and (
                existing_context is None
                or existing_context.conversation_id is None
                or existing_context.user_id == "anonymous"
            ):
                try:
                    conversation = await self._repository.get_conversation(conversation_id)
                except Exception:  # noqa: BLE001
                    conversation = None
                if conversation is not None:
                    refreshed_context = TraceContext(
                        conversation_id=conversation.id,
                        user_id=conversation.user_id,
                        tenant_id=conversation.tenant_id,
                    )
            async with self._restore_lock:
                if refreshed_context is not None:
                    self._trace_contexts[trace_id] = refreshed_context
                if legacy_root_span_id is None:
                    return True  # context-only recovery (record_event): no root needed
                if legacy_root_span_id not in self._spans:
                    self._drop_stale_roots(trace_id, legacy_root_span_id)
                    self._spans[legacy_root_span_id] = self._build_continuation_root(
                        trace_id, legacy_root_span_id, self._otel_trace_ids[trace_id]
                    )
            return True
        otel_trace_id = _otel_trace_id_for(trace_id)
        context: TraceContext | None = None
        root_span_id: str | None = None
        if self._obs_store is not None:
            try:
                row = await self._obs_store.get_trace(otel_trace_id)
            except Exception:  # noqa: BLE001 - recovery must never break business
                row = None
            if row is not None:
                otel_trace_id = row["trace_id"]
                root_span_id = row.get("root_span_id")
                context = TraceContext(
                    conversation_id=row.get("session_id"),
                    user_id=row.get("user_id") or "anonymous",
                    tenant_id=row.get("tenant_id") or "default",
                )
        # business-data fallback also applies when the OBS row exists but
        # carries no session id (review round 4 nit)
        if conversation_id and (
            context is None or context.conversation_id is None or context.user_id == "anonymous"
        ):
            try:
                conversation = await self._repository.get_conversation(conversation_id)
            except Exception:  # noqa: BLE001
                conversation = None
            if conversation is not None:
                context = TraceContext(
                    conversation_id=conversation.id,
                    user_id=conversation.user_id,
                    tenant_id=conversation.tenant_id,
                )
        if context is None:
            context = TraceContext(conversation_id=conversation_id, user_id="anonymous", tenant_id="default")
        async with self._restore_lock:
            if trace_id in self._otel_trace_ids:  # double-check under the lock
                current_context = self._trace_contexts.get(trace_id)
                if conversation_id and (
                    current_context is None
                    or current_context.conversation_id is None
                    or current_context.user_id == "anonymous"
                ):
                    self._trace_contexts[trace_id] = context
                if legacy_root_span_id is not None and legacy_root_span_id not in self._spans:
                    self._drop_stale_roots(trace_id, legacy_root_span_id)
                    self._spans[legacy_root_span_id] = self._build_continuation_root(
                        trace_id, legacy_root_span_id, self._otel_trace_ids[trace_id]
                    )
                return True
            self._otel_trace_ids[trace_id] = otel_trace_id
            self._otel_root_span_ids[trace_id] = root_span_id
            self._trace_contexts[trace_id] = context
            if legacy_root_span_id is None:
                # context-only recovery (record_event): the business root
                # span handle is built later by ensure_agent_root_span with
                # the caller's own span id (review round 5)
                return True
            legacy_root = legacy_root_span_id
            if legacy_root in self._spans:
                return True
            self._drop_stale_roots(trace_id, legacy_root)
            self._spans[legacy_root] = self._build_continuation_root(trace_id, legacy_root, otel_trace_id)
            return True

    def _drop_stale_roots(self, trace_id: str, keep_key: str) -> None:
        """Remove any other root span of this trace (e.g. the derived-key
        continuation root created by an earlier record_event recovery). They
        are dropped WITHOUT end() so no ghost span_finished records are
        produced (review round 5)."""
        for existing_key in list(self._spans):
            existing = self._spans[existing_key]
            if (
                existing_key != keep_key
                and existing.attributes.get(ATTR_LEGACY_TRACE_ID) == trace_id
                and existing.attributes.get(ATTR_SPAN_ROLE) == "root"
            ):
                self._spans.pop(existing_key, None)
                self._otel_span_ids.pop(existing_key, None)
                self._span_ids_by_trace.get(trace_id, set()).discard(existing_key)

    def _build_continuation_root(self, trace_id: str, legacy_root: str, otel_trace_id: str) -> Span:
        """Create a continuation root span pinned to the canonical trace id."""
        context = self._trace_contexts.get(trace_id)
        span = self._tracer.start_span("agent.run", context=_root_span_context(otel_trace_id))  # type: ignore[union-attr]
        span.set_attribute(ATTR_SPAN_ROLE, "root")
        span.set_attribute(ATTR_LEGACY_TRACE_ID, trace_id)
        span.set_attribute(ATTR_LEGACY_SPAN_ID, legacy_root)
        span.set_attribute(ATTR_SPAN_KIND, "agent")
        span.set_attribute(ATTR_SESSION_ID, context.conversation_id if context else "")
        span.set_attribute(ATTR_USER_ID, context.user_id if context else "anonymous")
        span.set_attribute(ATTR_TENANT_ID, context.tenant_id if context else "default")
        span.set_attribute(ATTR_CONVERSATION_ID, context.conversation_id if context else "")
        otel_root_span_id = span.context.span_id.to_bytes(8, "big").hex()
        self._otel_span_ids[legacy_root] = otel_root_span_id
        self._otel_root_span_ids[trace_id] = otel_root_span_id
        self._span_ids_by_trace.setdefault(trace_id, set()).add(legacy_root)
        return span

    def _next_sequence(self, otel_trace_id: str) -> int:
        sequence = self._trace_seq.get(otel_trace_id, 0) + 1
        self._trace_seq[otel_trace_id] = sequence
        return sequence

    def _new_otel_trace_id(self, legacy_trace_id: str) -> str:
        otel_id = _otel_trace_id_for(legacy_trace_id)
        self._otel_trace_ids[legacy_trace_id] = otel_id
        return otel_id

    def _start_span_handle(
        self,
        *,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
        kind: str,
        name: str,
        role: str,
        target_id: str | None = None,
        input_preview: str | None = None,
    ) -> Span | None:
        otel_trace_id = self._otel_trace_ids.get(trace_id)
        if otel_trace_id is None:
            return None
        context = None
        if parent_span_id is not None and parent_span_id in self._spans:
            context = otel_trace.set_span_in_context(self._spans[parent_span_id])
        elif role == "root":
            # The root span must carry the same trace_id as the trace_started /
            # trace_finished WAL records; otherwise Span rows are orphaned
            # from their Trace row (review P0-2).
            context = _root_span_context(otel_trace_id)
        span = self._tracer.start_span(name, context=context)  # type: ignore[union-attr]
        self._otel_span_ids[span_id] = span.context.span_id.to_bytes(8, "big").hex()
        self._span_ids_by_trace.setdefault(trace_id, set()).add(span_id)
        trace_context = self._trace_contexts.get(trace_id)
        span.set_attribute(ATTR_SPAN_ROLE, role)
        span.set_attribute(ATTR_LEGACY_TRACE_ID, trace_id)
        span.set_attribute(ATTR_LEGACY_SPAN_ID, span_id)
        span.set_attribute(ATTR_LEGACY_PARENT_SPAN_ID, parent_span_id or "")
        span.set_attribute(ATTR_SPAN_KIND, kind)
        span.set_attribute(ATTR_SEQUENCE_NO, self._next_sequence(otel_trace_id))
        span.set_attribute(ATTR_TARGET_ID, target_id or "")
        if trace_context is not None:
            span.set_attribute(ATTR_SESSION_ID, trace_context.conversation_id or "")
            span.set_attribute(ATTR_USER_ID, trace_context.user_id)
            span.set_attribute(ATTR_TENANT_ID, trace_context.tenant_id)
            span.set_attribute(ATTR_CONVERSATION_ID, trace_context.conversation_id or "")
        if input_preview is not None:
            span.set_attribute(ATTR_INPUT_PREVIEW, input_preview)
        self._spans[span_id] = span
        return span

    def _deterministic_event_id(self, trace_id: str, name: str, occurred_at: datetime) -> str:
        """Stable event primary key derived from content, so the direct-write
        fallback and WAL replay never produce duplicate event rows
        (review round 2)."""
        import hashlib

        digest = hashlib.sha256(f"{trace_id}:{name}:{occurred_at.isoformat()}".encode("utf-8")).hexdigest()
        return f"obsrec_{digest[:32]}"

    def _submit_started_record(
        self,
        *,
        otel_trace_id: str,
        span_id: str | None,
        record_type: Literal["trace_started", "span_started", "event"],
        payload: dict[str, Any],
        record_id: str | None = None,
    ) -> None:
        if self._wal_writer is None:
            return
        try:
            record_kwargs: dict[str, Any] = {
                "record_type": record_type,
                "producer_instance_id": self._wal_writer.producer_instance_id,
                "sequence_no": 0,
                "occurred_at": datetime.now(timezone.utc),
                "trace_id": otel_trace_id,
                "span_id": span_id,
                "payload": payload,
            }
            if record_id is not None:
                record_kwargs["record_id"] = record_id
            self._wal_writer.submit(ObsRecord(**record_kwargs))
        except WalError:
            logger.exception("WAL submit failed for %s record", record_type)

    async def _write_raw_io(
        self,
        span: Span,
        *,
        kind: str,
        input_data: Any | None,
        output_data: Any | None,
        error: dict[str, Any] | None,
    ) -> RawIoRef | None:
        if self._raw_io_writer is None:
            return None
        legacy_span_id = cast(str, span.attributes.get(ATTR_LEGACY_SPAN_ID))
        trace_id = span.context.trace_id.to_bytes(16, "big").hex()
        user_id = cast(str, span.attributes.get(ATTR_USER_ID)) or "anonymous"
        tenant_id = cast(str, span.attributes.get(ATTR_TENANT_ID)) or "default"
        if kind == "model":
            calls = self._pending_llm_calls.pop(legacy_span_id, [])
            call = calls[-1] if calls else None
            response = call.response if call is not None else output_data
            data: dict[str, Any] = {
                "request": call.request if call is not None else input_data,
                "response": response,
                "usage": (response or {}).get("usage") if isinstance(response, dict) else None,
                "error": call.error if call is not None else None,
                "attempts": [
                    {
                        "call_id": attempt.call_id,
                        "endpoint": attempt.endpoint,
                        "model": attempt.model,
                        "status": attempt.status,
                        "request": attempt.request,
                        "response": attempt.response,
                        "error": attempt.error,
                        "duration_ms": attempt.duration_ms,
                    }
                    for attempt in calls
                ],
            }
        else:
            data = {"input": input_data, "output": output_data, "error": error}
        return await self._raw_io_writer.write(
            kind=cast(Literal["model", "tool", "aina", "internal"], kind),
            trace_id=trace_id,
            span_id=_otel_span_id_for(legacy_span_id),
            tenant_id=tenant_id,
            user_id=user_id,
            data=data,
        )

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
                        input=summarize_trace_data(input_data),
                        attributes=cast(dict[str, Any], summarize_trace_data(attributes)),
                    )
                ],
            )
        )
        if not self._enabled:
            return True
        otel_trace_id = self._new_otel_trace_id(trace_id)
        self._trace_contexts[trace_id] = TraceContext(
            conversation_id=conversation_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        root_span_handle = self._start_span_handle(
            trace_id=trace_id,
            span_id=root_span_id,
            parent_span_id=None,
            kind="agent",
            name="agent.run",
            role="root",
            target_id="unibot",
            input_preview=_preview_json(input_data),
        )
        otel_root_span_id = (
            root_span_handle.context.span_id.to_bytes(8, "big").hex()
            if root_span_handle is not None
            else None
        )
        if otel_root_span_id is not None:
            self._otel_root_span_ids[trace_id] = otel_root_span_id
        self._submit_started_record(
            otel_trace_id=otel_trace_id,
            span_id=None,
            record_type="trace_started",
            payload={
                "legacy_trace_id": trace_id,
                # OTel span id (16 hex) fits the VARCHAR(32) column; the
                # legacy id is only kept in attributes (review e2e).
                "root_span_id": otel_root_span_id,
                "session_id": conversation_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "status": STATUS_RUNNING,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "attributes": cast(dict[str, Any], summarize_trace_data(attributes)),
            },
        )
        return True

    @observation_slice
    async def record_event(
        self,
        trace_id: str,
        *,
        kind: str,
        status: Literal["started", "completed", "failed", "pending", "approval_required"],
        conversation_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        duration_ms: float | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        # restore the trace context before touching the legacy repository so
        # approval events survive on a fresh instance (review round 5, P1)
        if self._enabled and (trace_id not in self._otel_trace_ids or conversation_id is not None):
            restored = await self._ensure_trace_context(trace_id, conversation_id=conversation_id)
            if not restored:
                return
        # build the TraceEvent once so the repository row, the WAL record and
        # the deterministic event id all share the same timestamp (review)
        event = TraceEvent(
            kind=kind,
            status=status,
            target_type=target_type,
            target_id=target_id,
            duration_ms=duration_ms,
            details=cast(dict[str, Any], summarize_trace_data(details or {})),
        )
        try:
            await self._repository.add_trace_event(trace_id, event)
        except Exception:
            logger.warning("legacy trace event persistence skipped for %s", trace_id, exc_info=True)
        if not self._enabled or trace_id not in self._otel_trace_ids:
            return
        otel_trace_id = self._otel_trace_ids[trace_id]
        context = self._trace_contexts.get(trace_id)
        attributes: dict[str, Any] = {
            **(details or {}),
            "target_type": target_type,
            "target_id": target_id,
            "duration_ms": duration_ms,
        }
        occurred_at = event.timestamp
        self._submit_started_record(
            otel_trace_id=otel_trace_id,
            span_id=None,
            record_type="event",
            record_id=self._deterministic_event_id(otel_trace_id, kind, occurred_at),
            payload={
                "session_id": context.conversation_id if context else None,
                "user_id": context.user_id if context else "anonymous",
                "tenant_id": context.tenant_id if context else "default",
                "name": kind,
                "status": status,
                "occurred_at": occurred_at.isoformat(),
                "attributes": cast(dict[str, Any], summarize_trace_data(attributes)),
            },
        )

    @observation_slice
    async def record_llm_call(self, call: LLMCallRecord) -> None:
        sanitized = call.model_copy(
            update={
                "request": summarize_trace_data(call.request),
                "response": summarize_trace_data(call.response),
                "error": summarize_trace_data(call.error),
            }
        )
        await self._repository.upsert_llm_call(sanitized)
        # New path: stage the call as raw-IO source for its Model Span; the
        # Span finished record will carry the raw_io reference.
        if self._enabled and call.trace_id in self._otel_trace_ids and call.span_id:
            attempts = self._pending_llm_calls.setdefault(call.span_id, [])
            for index, attempt in enumerate(attempts):
                if attempt.call_id == call.call_id:
                    attempts[index] = call
                    break
            else:
                attempts.append(call)

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
        # restore the trace context BEFORE touching the legacy repository:
        # on a fresh instance the in-memory trace does not exist and the
        # repository call would raise, so recovery must happen first
        # (review round 4, P1)
        if self._enabled and trace_id not in self._otel_trace_ids:
            restored = await self._ensure_trace_context(trace_id, parent_span_id)
            if not restored:
                return
        try:
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
                    input=summarize_trace_data(input_data),
                    attributes=cast(dict[str, Any], summarize_trace_data(attributes or {})),
                ),
            )
        except Exception:
            # on a fresh instance the legacy trace does not exist in memory;
            # the OTel pipeline below must continue regardless
            # (review round 4, P1)
            logger.warning("legacy trace span persistence skipped for %s", trace_id, exc_info=True)
        if not self._enabled:
            return
        otel_trace_id = self._otel_trace_ids[trace_id]
        span = self._start_span_handle(
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            kind=kind,
            name=name,
            role="child",
            target_id=target_id,
            input_preview=_preview_json(input_data),
        )
        if span is not None:
            self._submit_started_record(
                otel_trace_id=otel_trace_id,
                span_id=span.context.span_id.to_bytes(8, "big").hex(),
                record_type="span_started",
                payload={
                    "legacy_span_id": span_id,
                    # OTel parent span id (16 hex), fits VARCHAR(32)
                    "parent_span_id": (
                        span.parent.span_id.to_bytes(8, "big").hex()
                        if span.parent is not None
                        else None
                    ),
                    "sequence_no": cast(int, span.attributes.get(ATTR_SEQUENCE_NO)),
                    "session_id": self._trace_contexts[trace_id].conversation_id,
                    "user_id": self._trace_contexts[trace_id].user_id,
                    "tenant_id": self._trace_contexts[trace_id].tenant_id,
                    "kind": kind,
                    "name": name,
                    "target_id": target_id,
                    "status": STATUS_RUNNING,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "attributes": cast(dict[str, Any], summarize_trace_data(attributes or {})),
                },
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
        if not self._enabled:
            await self._repository.finish_trace_span(
                trace_id,
                span_id,
                status,
                input_data=summarize_trace_data(input_data),
                output_data=summarize_trace_data(output_data),
                attributes=(
                    cast(dict[str, Any], summarize_trace_data(attributes))
                    if attributes is not None
                    else None
                ),
                first_output_at=first_output_at,
                error=cast(dict[str, Any], summarize_trace_data(error)) if error is not None else None,
            )
            return
        span = self._spans.get(span_id)
        if span is None:
            await self._repository.finish_trace_span(
                trace_id,
                span_id,
                status,
                input_data=summarize_trace_data(input_data),
                output_data=summarize_trace_data(output_data),
                attributes=(
                    cast(dict[str, Any], summarize_trace_data(attributes))
                    if attributes is not None
                    else None
                ),
                first_output_at=first_output_at,
                error=cast(dict[str, Any], summarize_trace_data(error)) if error is not None else None,
            )
            return
        try:
            kind = cast(str, span.attributes.get(ATTR_SPAN_KIND)) or "internal"
            if kind == "model" and span_id in self._pending_llm_calls:
                calls = self._pending_llm_calls[span_id]
                span.set_attribute(
                    "gen_ai.response.model",
                    calls[-1].model,
                )
            raw_ref: RawIoRef | None = None
            try:
                raw_ref = await self._write_raw_io(
                    span,
                    kind=kind,
                    input_data=input_data,
                    output_data=output_data,
                    error=error,
                )
            except Exception:
                # raw IO persistence failure must not skip span.end(): the
                # span_finished record (with tokens/status) still needs to be
                # produced (review round 5)
                logger.exception("raw IO write failed for span %s", span_id)
            # persist raw IO refs into the legacy span attributes too, so the
            # direct-write fallback can reproduce them (review round 2)
            merged_attributes: dict[str, Any] | None = None
            if attributes is not None:
                merged_attributes = cast(dict[str, Any], summarize_trace_data(attributes))
            if raw_ref is not None:
                if merged_attributes is None:
                    merged_attributes = {}
                merged_attributes.update(raw_ref.to_span_attributes())
            try:
                await self._repository.finish_trace_span(
                    trace_id,
                    span_id,
                    status,
                    input_data=summarize_trace_data(input_data),
                    output_data=summarize_trace_data(output_data),
                    attributes=merged_attributes,
                    first_output_at=first_output_at,
                    error=cast(dict[str, Any], summarize_trace_data(error)) if error is not None else None,
                )
            except Exception:
                # fresh instance: legacy trace missing in memory; the OTel
                # span below must still finish (review round 5, P1)
                logger.warning("legacy trace span finish skipped for %s", trace_id, exc_info=True)
            span.set_attribute(UNIBOT_STATUS_ATTR, status)
            if attributes and attributes.get("ttft_ms") is not None:
                span.set_attribute(ATTR_TTFT_MS, float(attributes["ttft_ms"]))
            # the root span carries the conversation-wide usage totals; keep
            # them for finish_trace aggregation (the span is ended/popped
            # before finish_trace runs, review P1-4)
            if (
                cast(str, span.attributes.get(ATTR_SPAN_ROLE)) == "root"
                and trace_id not in self._trace_token_totals
            ):
                self._trace_token_totals[trace_id] = (
                    int(attributes.get("input_tokens") or 0) if attributes else 0,
                    int(attributes.get("output_tokens") or 0) if attributes else 0,
                    int(attributes.get("cache_read_tokens") or 0) if attributes else 0,
                )
            for key in (ATTR_INPUT_TOKENS, ATTR_OUTPUT_TOKENS):
                if attributes and key in attributes:
                    span.set_attribute(key, int(attributes[key]))
            if attributes and "input_tokens" in attributes:
                span.set_attribute(ATTR_INPUT_TOKENS, int(attributes["input_tokens"]))
            if attributes and "output_tokens" in attributes:
                span.set_attribute(ATTR_OUTPUT_TOKENS, int(attributes["output_tokens"]))
            if attributes and "cache_read_tokens" in attributes:
                span.set_attribute("gen_ai.usage.cache_read.input_tokens", int(attributes["cache_read_tokens"]))
            if attributes and "usage_estimated" in attributes:
                span.set_attribute("usage_estimated", bool(attributes["usage_estimated"]))
            if first_output_at is not None:
                span.set_attribute(ATTR_FIRST_OUTPUT_AT, first_output_at.isoformat())
            if input_data is not None:
                span.set_attribute(ATTR_INPUT_PREVIEW, _preview_json(input_data) or "")
            if output_data is not None:
                span.set_attribute(ATTR_OUTPUT_PREVIEW, _preview_json(output_data) or "")
            if error is not None:
                span.set_attribute(ATTR_ERROR_JSON, json.dumps(summarize_trace_data(error), ensure_ascii=False, default=str))
            if raw_ref is not None:
                if raw_ref.path is not None:
                    span.set_attribute(ATTR_RAW_IO_PATH, raw_ref.path)
                if raw_ref.sha256 is not None:
                    span.set_attribute(ATTR_RAW_IO_SHA256, raw_ref.sha256)
                if raw_ref.size_bytes is not None:
                    span.set_attribute(ATTR_RAW_IO_SIZE, raw_ref.size_bytes)
                span.set_attribute(ATTR_RAW_IO_STATUS, raw_ref.status)
            span.end()
        finally:
            self._spans.pop(span_id, None)

    @observation_slice
    async def finish_trace(self, trace_id: str, status: str) -> None:
        # restore the trace context first: on a fresh instance the legacy
        # repository has no trace, so finish_trace would raise before
        # recovery ran (review round 4, P1)
        if self._enabled and trace_id not in self._otel_trace_ids:
            restored = await self._ensure_trace_context(trace_id)
            if not restored:
                # legacy path still runs (best effort; it raises on a fresh
                # instance and observation_slice swallows it)
                await self._repository.finish_trace(trace_id, status)
                return
        try:
            await self._repository.finish_trace(trace_id, status)
        except Exception:
            # fresh instance: legacy trace missing; the OTel pipeline below
            # must still complete (review round 4, P1)
            logger.warning("legacy trace finish skipped for %s", trace_id)
        if not self._enabled:
            return
        otel_trace_id = self._otel_trace_ids[trace_id]
        trace: TraceRecord | None = None
        try:
            trace = await self._repository.get_trace(trace_id)
        except Exception:
            logger.exception("finish_trace could not read legacy trace for aggregation")
        root_span_id = trace.root_span_id if trace is not None else None
        root_span = self._spans.get(root_span_id) if root_span_id else None
        if root_span is None:
            for candidate_id, candidate in self._spans.items():
                if (
                    candidate.attributes.get(ATTR_LEGACY_TRACE_ID) == trace_id
                    and candidate.attributes.get(ATTR_SPAN_ROLE) == "root"
                ):
                    root_span_id = candidate_id
                    root_span = candidate
                    break

        mapped_root_span_id = self._otel_root_span_ids.get(trace_id)
        root_recorded_locally = mapped_root_span_id is not None and any(
            self._otel_span_ids.get(span_id) == mapped_root_span_id
            for span_id in self._span_ids_by_trace.get(trace_id, set())
        )
        if status != STATUS_APPROVAL_REQUIRED and root_span is None and not root_recorded_locally:
            # Deny/cancel can terminate a trace immediately after context-only
            # recovery. Build an exportable continuation root before the
            # trace_finished record and its durability barrier.
            root_span_id = root_span_id or f"span_{uuid.uuid4().hex}"
            root_span = self._build_continuation_root(trace_id, root_span_id, otel_trace_id)
            self._spans[root_span_id] = root_span
        if root_span is not None and root_span.is_recording():
            root_span.set_attribute(UNIBOT_STATUS_ATTR, status)
            root_span.set_attribute(ATTR_TRACE_STATUS, status)
            # Export the current root even at approval_required. A process
            # restart cannot recover a live SDK span; ending it here keeps
            # pre-approval child spans connected to a persisted root. Resume
            # builds a continuation root under the same trace id.
            root_span.end()
            self._spans.pop(root_span_id, None)

        context = self._trace_contexts.get(trace_id)
        events = trace.events if trace is not None else []
        spans = trace.spans if trace is not None else []
        # Aggregate tokens from the legacy in-memory spans: the root span is
        # ended and popped before finish_trace runs, so reading token totals
        # from the root OTel span attributes would always yield zero
        # (review P1-4). The root span's conversation totals are captured in
        # finish_span; fall back to summing child spans (excluding the root,
        # which repeats the same totals).
        root_totals = self._trace_token_totals.get(trace_id)
        if root_totals is not None:
            input_tokens, output_tokens, cache_read_tokens = root_totals
        else:
            child_spans = [s for s in spans if s.kind != "agent"]
            input_tokens = sum(int(s.attributes.get("input_tokens") or 0) for s in child_spans)
            output_tokens = sum(int(s.attributes.get("output_tokens") or 0) for s in child_spans)
            cache_read_tokens = sum(int(s.attributes.get("cache_read_tokens") or 0) for s in child_spans)
        compression_count = sum(1 for event in events if "compression" in event.kind)
        error_count = sum(1 for span in spans if span.status == "failed") + sum(
            1 for event in events if event.status == "failed"
        )
        message_count = sum(
            1 for event in events if event.kind in ("user.request", "final.response")
        )
        durable = True
        terminal_sequence: int | None = None
        if self._wal_writer is not None:
            try:
                terminal_sequence = self._wal_writer.submit(
                    ObsRecord(
                        record_type="trace_finished",
                        producer_instance_id=self._wal_writer.producer_instance_id,
                        sequence_no=0,
                        occurred_at=datetime.now(timezone.utc),
                        trace_id=otel_trace_id,
                        span_id=None,
                        payload={
                            "legacy_trace_id": trace_id,
                            # OTel root span id (16 hex), fits VARCHAR(32)
                            "root_span_id": self._otel_root_span_ids.get(trace_id),
                            "session_id": context.conversation_id if context else None,
                            "user_id": context.user_id if context else "anonymous",
                            "tenant_id": context.tenant_id if context else "default",
                            "status": status,
                            "started_at": (
                                trace.created_at.isoformat()
                                if trace is not None
                                else datetime.now(timezone.utc).isoformat()
                            ),
                            "completed_at": (
                                trace.completed_at.isoformat()
                                if trace is not None and trace.completed_at
                                else None
                            ),
                            "duration_ms": (
                                (trace.completed_at - trace.created_at).total_seconds() * 1000.0
                                if trace is not None and trace.completed_at and trace.created_at
                                else None
                            ),
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "cache_read_tokens": cache_read_tokens,
                            "message_count": message_count,
                            "compression_count": compression_count,
                            "error_count": error_count,
                            "attributes": {},
                        },
                    )
                )
            except WalError:
                logger.exception("WAL submit failed for trace_finished %s", trace_id)
                durable = False
            # Trace barrier: the completed interaction must be durable before
            # the business returns its final answer (design 6.2). A barrier
            # failure is retried and, if permanent, reported loudly so the
            # gap is discoverable (design 15; review P1-5).
            if terminal_sequence is not None:
                try:
                    await self._flush_with_retry(terminal_sequence)
                except WalGapError:
                    logger.error(
                        "WAL barrier failed for finished trace %s: telemetry gap, data is not durable", trace_id
                    )
                    durable = False
                except WalError:
                    logger.exception("WAL barrier failed for finished trace %s", trace_id)
                    durable = False
        if not durable and self._obs_store is not None:
            # design 14 fallback: WAL/NAS unavailable but MySQL healthy ->
            # write the trace rows directly to OBS MySQL (review P1-4).
            try:
                await self._obs_store.bulk_upsert(
                    self._build_fallback_records(trace_id, status, trace)
                )
                logger.warning("WAL barrier failed; OBS rows written directly to MySQL for trace %s", trace_id)
                durable = True
            except Exception:
                logger.exception("OBS direct-write fallback failed for trace %s", trace_id)
        if durable and status != STATUS_APPROVAL_REQUIRED:
            self._cleanup_trace_state(trace_id)

    def _build_fallback_records(
        self,
        trace_id: str,
        status: str,
        trace: TraceRecord | None,
    ) -> list[ObsRecord]:
        """Reconstruct OBS records from the in-memory legacy trace for the
        direct-write fallback (design 14)."""
        import json as _json

        otel_trace_id = self._otel_trace_ids.get(trace_id, _otel_trace_id_for(trace_id))
        context = self._trace_contexts.get(trace_id)
        records: list[ObsRecord] = []
        trace_finished = ObsRecord(
            record_type="trace_finished",
            producer_instance_id=(
                self._wal_writer.producer_instance_id if self._wal_writer is not None else "fallback"
            ),
            sequence_no=0,
            occurred_at=datetime.now(timezone.utc),
            trace_id=otel_trace_id,
            span_id=None,
            payload={
                "legacy_trace_id": trace_id,
                "root_span_id": self._otel_root_span_ids.get(trace_id),
                "session_id": context.conversation_id if context else None,
                "user_id": context.user_id if context else "anonymous",
                "tenant_id": context.tenant_id if context else "default",
                "status": status,
                "started_at": (
                    trace.created_at.isoformat()
                    if trace is not None
                    else datetime.now(timezone.utc).isoformat()
                ),
                "completed_at": (
                    trace.completed_at.isoformat() if trace is not None and trace.completed_at else None
                ),
                "duration_ms": (
                    (trace.completed_at - trace.created_at).total_seconds() * 1000.0
                    if trace is not None and trace.completed_at and trace.created_at
                    else None
                ),
                "input_tokens": self._trace_token_totals.get(trace_id, (0, 0, 0))[0],
                "output_tokens": self._trace_token_totals.get(trace_id, (0, 0, 0))[1],
                "cache_read_tokens": self._trace_token_totals.get(trace_id, (0, 0, 0))[2],
                "message_count": sum(
                    1 for event in (trace.events if trace else []) if event.kind in ("user.request", "final.response")
                ),
                "compression_count": sum(
                    1 for event in (trace.events if trace else []) if "compression" in event.kind
                ),
                "error_count": sum(1 for span in (trace.spans if trace else []) if span.status == "failed")
                + sum(1 for event in (trace.events if trace else []) if event.status == "failed"),
                "attributes": {},
            },
        )
        records.append(trace_finished)
        for span in trace.spans if trace is not None else []:
            span_attributes = dict(span.attributes or {})
            otel_span_id = self._otel_span_ids.get(span.span_id) or _derived_span_id(span.span_id)
            parent_otel_id = (
                self._otel_span_ids.get(span.parent_span_id)
                or (_derived_span_id(span.parent_span_id) if span.parent_span_id else None)
            )
            records.append(
                ObsRecord(
                    record_type="span_finished",
                    producer_instance_id=(
                        self._wal_writer.producer_instance_id if self._wal_writer is not None else "fallback"
                    ),
                    sequence_no=0,
                    # version = the span's own finish time, so a still-running
                    # legacy span (low version) can never downgrade a finished
                    # row that is already in MySQL (review round 2)
                    occurred_at=span.completed_at or span.started_at,
                    trace_id=otel_trace_id,
                    # real 16-hex OTel span id fits VARCHAR(32); legacy ids are
                    # 37 chars and would break the fallback (review round 3)
                    span_id=otel_span_id,
                    payload={
                        "legacy_span_id": span.span_id,
                        "parent_span_id": parent_otel_id,
                        "sequence_no": 0,
                        "session_id": context.conversation_id if context else None,
                        "user_id": context.user_id if context else "anonymous",
                        "tenant_id": context.tenant_id if context else "default",
                        "kind": span.kind,
                        "name": span.name,
                        "target_id": span.target_id,
                        "status": span.status,
                        "started_at": span.started_at.isoformat(),
                        "completed_at": span.completed_at.isoformat() if span.completed_at else None,
                        "duration_ms": span.duration_ms,
                        "input_tokens": int(span_attributes.get("input_tokens") or 0),
                        "output_tokens": int(span_attributes.get("output_tokens") or 0),
                        "cache_read_tokens": int(span_attributes.get("cache_read_tokens") or 0),
                        "input_preview": _json.dumps(span.input, ensure_ascii=False) if span.input is not None else None,
                        "output_preview": _json.dumps(span.output, ensure_ascii=False) if span.output is not None else None,
                        "attributes": span.attributes,
                        "error": span.error,
                        # raw IO refs were persisted into the legacy span
                        # attributes by finish_span, so the fallback reproduces
                        # them instead of wiping rows already in MySQL
                        "raw_io_path": span_attributes.get("unibot.raw_io.path"),
                        "raw_io_sha256": span_attributes.get("unibot.raw_io.sha256"),
                        "raw_io_size_bytes": span_attributes.get("unibot.raw_io.size_bytes"),
                        "raw_io_status": span_attributes.get("unibot.raw_io.status") or "not_applicable",
                    },
                )
            )
        for event in trace.events if trace is not None else []:
            records.append(
                ObsRecord(
                    record_id=self._deterministic_event_id(otel_trace_id, event.kind, event.timestamp),
                    record_type="event",
                    producer_instance_id=(
                        self._wal_writer.producer_instance_id if self._wal_writer is not None else "fallback"
                    ),
                    sequence_no=0,
                    occurred_at=event.timestamp,
                    trace_id=otel_trace_id,
                    span_id=None,
                    payload={
                        "session_id": context.conversation_id if context else None,
                        "user_id": context.user_id if context else "anonymous",
                        "tenant_id": context.tenant_id if context else "default",
                        "name": event.kind,
                        "status": event.status,
                        "occurred_at": event.timestamp.isoformat(),
                        "attributes": event.details,
                    },
                )
            )
        return records

    def _cleanup_trace_state(self, trace_id: str) -> None:
        """Drop per-trace runtime state after a terminal finish (review P2-1).
        approval_required keeps its state for the confirmation resume path."""
        otel_trace_id = self._otel_trace_ids.pop(trace_id, None)
        self._otel_root_span_ids.pop(trace_id, None)
        self._trace_contexts.pop(trace_id, None)
        self._trace_token_totals.pop(trace_id, None)
        if otel_trace_id is not None:
            self._trace_seq.pop(otel_trace_id, None)
        for span_id, calls in list(self._pending_llm_calls.items()):
            if any(call.trace_id == trace_id for call in calls):
                self._pending_llm_calls.pop(span_id, None)
        for legacy_span_id, span in list(self._spans.items()):
            if span.attributes.get(ATTR_LEGACY_TRACE_ID) == trace_id:
                if span.is_recording():
                    span.end()
                self._spans.pop(legacy_span_id, None)
                self._otel_span_ids.pop(legacy_span_id, None)
        # drop the whole trace's span-id mappings, including spans that were
        # already finished and removed from _spans (review round 4, P2)
        for legacy_span_id in self._span_ids_by_trace.pop(trace_id, set()):
            self._otel_span_ids.pop(legacy_span_id, None)

    async def _flush_with_retry(self, sequence_no: int, *, attempts: int = 3) -> None:
        """flush_through with short retries; gaps are permanent and re-raised."""
        for attempt in range(attempts):
            try:
                await self._wal_writer.flush_through(sequence_no)  # type: ignore[union-attr]
                return
            except WalGapError:
                raise
            except WalError:
                if attempt == attempts - 1:
                    raise
                await asyncio.sleep(0.05 * (attempt + 1))

    async def record_cancelled_approvals(self, approvals: list[ApprovalRecord]) -> None:
        for approval in approvals:
            await self.record_event(
                approval.trace_id,
                kind="approval.cancelled",
                status="completed",
                conversation_id=approval.conversation_id,
                details={"approval_id": approval.id},
            )
            await self.finish_trace(approval.trace_id, "completed")

    async def ensure_agent_root_span(self, trace_id: str, *, span_id: str, conversation_id: str) -> str:
        # cross-instance approval resume enters through here with the
        # conversation id available. Reuse a live legacy root for the initial
        # run, but use the caller's fresh id after an approval boundary: the
        # paused root has already been exported and legacy_span_id is unique.
        real_root = span_id
        if self._enabled:
            try:
                trace = await self._repository.get_trace(trace_id)
                if trace is not None and trace.root_span_id in self._spans:
                    real_root = trace.root_span_id
            except Exception:
                pass  # fresh instance: fall back to the caller's span_id
            await self._ensure_trace_context(trace_id, real_root, conversation_id)
        fallback = TraceSpan(
            span_id=real_root,
            kind="agent",
            name="agent.run",
            target_id="unibot",
            attributes={"conversation_id": conversation_id, "migrated_trace": True},
        )
        try:
            trace = await self._repository.get_trace(trace_id)
            if trace.root_span_id is not None:
                if self._enabled and real_root != trace.root_span_id:
                    await self._repository.add_trace_span(trace_id, fallback)
                    return real_root
                return trace.root_span_id
            span = fallback.model_copy(update={"started_at": trace.created_at})
            root = await self._repository.ensure_trace_root_span(trace_id, span)
            return root.span_id
        except Exception:
            logger.exception("Observability operation ensure_root_span failed")
            return fallback.span_id

    # ---- observability of the observability (design section 15) ----

    def health_snapshot(self) -> dict[str, Any]:
        snapshot: dict[str, Any] = {
            "obs_enabled": self._enabled,
            "obs_open_spans": len(self._spans),
            "obs_pending_llm_calls": len(self._pending_llm_calls),
        }
        if self._wal_writer is not None:
            snapshot.update(self._wal_writer.metrics.snapshot())
        return snapshot
