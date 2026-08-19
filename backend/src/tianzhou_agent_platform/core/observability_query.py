"""Permission-aware OBS page aggregation queries (design section 12).

Every access goes through this service:

- normal users always carry ``tenant_id`` + ``user_id`` filters
- platform-admin queries drop the user filter after ``require_platform_admin``
- raw IO is only served after the owning Span's user/tenant has been checked
- all list endpoints are time/range-bounded, never unbounded

DTO shapes follow what the existing OBS pages consume (Token totals, rates,
per-model breakdown, daily Calendar, reader-friendly span tree, error
diagnosis and raw log references). When the OBS store is not configured the
service degrades to empty results so the API surface stays stable during
migration (legacy fallback is handled by the API layer).
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tianzhou_agent_platform.core.context_compression import estimate_request_tokens
from tianzhou_agent_platform.store.observability_store import ObservabilityStore

logger = logging.getLogger(__name__)

RANGE_DAYS = {"day": 1, "week": 7, "month": 30}

MAX_RAW_LOG_BYTES = 64 * 1024 * 1024


def _bounded_gzip_decompress(data: bytes, max_bytes: int) -> bytes:
    """Decompress a gzip stream with a hard output bound (zip-bomb guard)."""
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    output = decompressor.decompress(data, max_bytes + 1)
    if decompressor.unconsumed_tail or len(output) > max_bytes or not decompressor.eof:
        raise ValueError("raw log exceeds the decompressed size limit")
    return output


def range_bounds(range_name: str, *, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return ``(start, end)`` for day/week/month ranges (UTC)."""
    current = now or datetime.now(timezone.utc)
    days = RANGE_DAYS.get(range_name, 7)
    start = (current - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, current + timedelta(seconds=1)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _preview_json(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return str(value)


def _span_usage(row: dict[str, Any], input_preview: Any, output_preview: Any) -> tuple[int, int, dict[str, Any]]:
    input_tokens = int(row.get("input_tokens") or 0)
    output_tokens = int(row.get("output_tokens") or 0)
    attributes = dict(row.get("attributes") or {})
    if (
        row.get("kind") != "model"
        or row.get("status") != "completed"
        or input_tokens != 0
        or output_tokens != 0
    ):
        return input_tokens, output_tokens, attributes

    request = input_preview if isinstance(input_preview, dict) else {}
    response = output_preview if isinstance(output_preview, dict) else None
    try:
        input_tokens = max(0, int(request.get("estimated_prompt_tokens") or 0))
    except (TypeError, ValueError):
        input_tokens = 0
    if input_tokens == 0 and isinstance(request.get("messages"), list):
        input_tokens = estimate_request_tokens(request["messages"], request.get("tools"))
    if response is not None:
        output_tokens = estimate_request_tokens([response])
    if input_tokens > 0 or output_tokens > 0:
        attributes["usage_estimated"] = True
        attributes["usage_source"] = "estimated"
    return input_tokens, output_tokens, attributes


class ObsQueryService:
    def __init__(self, store: ObservabilityStore | None, raw_io_root: Path | None) -> None:
        self._store = store
        self._raw_io_root = raw_io_root

    @property
    def enabled(self) -> bool:
        return self._store is not None

    # ---- personal views (section 12.2) ----

    async def personal_overview(
        self,
        *,
        tenant_id: str,
        user_id: str,
        range_name: str = "week",
    ) -> dict[str, Any]:
        start, end = range_bounds(range_name)
        if self._store is None:
            return self._empty_overview(range_name)
        summary, daily, models = await asyncio.gather(
            self._store.aggregate_tokens(
                tenant_id=tenant_id,
                user_id=user_id,
                started_after=start,
                started_before=end,
            ),
            self._store.daily_tokens(
                tenant_id=tenant_id,
                user_id=user_id,
                started_after=start,
                started_before=end,
            ),
            self._store.model_breakdown(
                tenant_id=tenant_id,
                user_id=user_id,
                started_after=start,
                started_before=end,
            ),
        )
        input_tokens = int(summary.get("input_tokens") or 0)
        output_tokens = int(summary.get("output_tokens") or 0)
        cache_read_tokens = int(summary.get("cache_read_tokens") or 0)
        # cache_read is a subset of input tokens (OTel GenAI semantics);
        # counting it again in the total would double-count (review).
        total_tokens = input_tokens + output_tokens
        return {
            "range": range_name,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "trace_count": int(summary.get("trace_count") or 0),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "total_tokens": total_tokens,
            "error_count": int(summary.get("error_count") or 0),
            "active_days": int(summary.get("active_days") or 0),
            "conversation_count": int(summary.get("conversation_count") or 0),
            "per_model": [
                {
                    "model": row["model"],
                    "call_count": int(row["call_count"] or 0),
                    "input_tokens": int(row["input_tokens"] or 0),
                    "output_tokens": int(row["output_tokens"] or 0),
                    "cache_read_tokens": int(row["cache_read_tokens"] or 0),
                }
                for row in models
            ],
            "daily": [
                {
                    "day": _iso(row["day"]),
                    "trace_count": int(row["trace_count"] or 0),
                    "input_tokens": int(row["input_tokens"] or 0),
                    "output_tokens": int(row["output_tokens"] or 0),
                    "cache_read_tokens": int(row["cache_read_tokens"] or 0),
                }
                for row in daily
            ],
        }

    async def session_detail(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        limit: int = 100,
    ) -> dict[str, Any] | None:
        if self._store is None:
            return None
        traces = await self._store.list_traces(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            limit=max(1, min(limit, 500)),
        )
        if not traces:
            return None
        trace_ids = [row["trace_id"] for row in traces]
        span_lists = await asyncio.gather(*(self._store.list_spans(trace_id) for trace_id in trace_ids))
        event_lists = await asyncio.gather(*(self._store.list_events(trace_id) for trace_id in trace_ids))
        span_id_maps = {
            trace_id: self._span_identity_map(trace_spans)
            for trace_id, trace_spans in zip(trace_ids, span_lists, strict=True)
        }
        spans: list[dict[str, Any]] = [span for spans_for_trace in span_lists for span in spans_for_trace]
        events: list[dict[str, Any]] = [event for events_for_trace in event_lists for event in events_for_trace]
        spans.sort(key=lambda row: (row.get("sequence_no") or 0, _iso(row.get("started_at")) or ""))
        return {
            "session_id": session_id,
            "traces": [
                self._trace_dto(row, span_id_maps.get(row["trace_id"])) for row in traces
            ],
            "spans": [
                self._span_dto(row, span_id_maps.get(row["trace_id"])) for row in spans
            ],
            "events": [
                self._event_dto(row, span_id_maps.get(row["trace_id"]))
                for row in events
            ],
        }

    async def raw_log(
        self,
        *,
        tenant_id: str,
        user_id: str,
        trace_id: str,
        span_id: str,
    ) -> dict[str, Any] | None:
        """Serve a raw IO document only after ownership of the owning Span is
        verified (design 12.1: never read by file path alone)."""
        if self._store is None or self._raw_io_root is None:
            return None
        span = await self._store.get_span(span_id)
        if span is None:
            return None
        if span["tenant_id"] != tenant_id or span["user_id"] != user_id:
            logger.warning("raw log ownership mismatch for span %s (tenant/user)", span_id)
            return None
        if span["trace_id"] != trace_id:
            return None
        raw_path = span.get("raw_io_path")
        if not raw_path or span.get("raw_io_status") not in ("ready",):
            return {"status": span.get("raw_io_status") or "not_applicable", "detail": None}
        target = (self._raw_io_root / raw_path).resolve(strict=False)
        if not target.is_relative_to(self._raw_io_root.resolve(strict=False)):
            return None
        max_bytes = int(span.get("raw_io_size_bytes") or 0)
        try:
            actual_size = (await asyncio.to_thread(target.stat)).st_size
        except OSError:
            return {"status": "failed", "detail": None}
        if max(actual_size, max_bytes) > 64 * 1024 * 1024:
            return {"status": "too_large", "detail": None}
        try:
            content = await asyncio.to_thread(target.read_bytes)
            expected_sha256 = span.get("raw_io_sha256")
            if expected_sha256 and not hmac.compare_digest(
                hashlib.sha256(content).hexdigest(),
                str(expected_sha256),
            ):
                logger.error("raw log checksum mismatch for %s", raw_path)
                return {"status": "failed", "detail": None}
            # bounded decompression: never trust the gzip stream size
            document = json.loads(_bounded_gzip_decompress(content, 64 * 1024 * 1024).decode("utf-8"))
        except (OSError, ValueError):
            logger.exception("raw log read failed for %s", raw_path)
            return {"status": "failed", "detail": None}
        return {"status": "ready", "detail": document}

    # ---- admin views ----

    async def admin_overview(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        range_name: str = "week",
    ) -> dict[str, Any]:
        start, end = range_bounds(range_name)
        if self._store is None:
            return self._empty_overview(range_name)
        # admin overview reuses per-user aggregation when a user is selected,
        # otherwise aggregates across the whole tenant (or all tenants).
        summary, daily, models = await asyncio.gather(
            self._store.aggregate_tokens(
                tenant_id=tenant_id,
                user_id=user_id,
                started_after=start,
                started_before=end,
            ),
            self._store.daily_tokens(
                tenant_id=tenant_id,
                user_id=user_id,
                started_after=start,
                started_before=end,
            ),
            self._store.model_breakdown(
                tenant_id=tenant_id,
                user_id=user_id,
                started_after=start,
                started_before=end,
            ),
        )
        input_tokens = int(summary.get("input_tokens") or 0)
        output_tokens = int(summary.get("output_tokens") or 0)
        cache_read_tokens = int(summary.get("cache_read_tokens") or 0)
        # cache_read is a subset of input tokens; avoid double-counting (review)
        return {
            "range": range_name,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "trace_count": int(summary.get("trace_count") or 0),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "total_tokens": input_tokens + output_tokens,
            "error_count": int(summary.get("error_count") or 0),
            "active_days": int(summary.get("active_days") or 0),
            "conversation_count": int(summary.get("conversation_count") or 0),
            "per_model": [
                {
                    "model": row["model"],
                    "call_count": int(row["call_count"] or 0),
                    "input_tokens": int(row["input_tokens"] or 0),
                    "output_tokens": int(row["output_tokens"] or 0),
                    "cache_read_tokens": int(row["cache_read_tokens"] or 0),
                }
                for row in models
            ],
            "daily": [
                {
                    "day": _iso(row["day"]),
                    "trace_count": int(row["trace_count"] or 0),
                    "input_tokens": int(row["input_tokens"] or 0),
                    "output_tokens": int(row["output_tokens"] or 0),
                    "cache_read_tokens": int(row["cache_read_tokens"] or 0),
                }
                for row in daily
            ],
        }

    async def admin_session_detail(
        self,
        *,
        session_id: str,
        tenant_id: str | None = None,
        user_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any] | None:
        if self._store is None:
            return None
        traces = await self._store.list_traces(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            limit=max(1, min(limit, 500)),
        )
        if not traces:
            return None
        owner_user = traces[0]["user_id"]
        owner_tenant = traces[0]["tenant_id"]
        all_traces = await self._store.list_traces(
            tenant_id=owner_tenant,
            user_id=owner_user,
            session_id=session_id,
            limit=max(1, min(limit, 500)),
        )
        trace_ids = [row["trace_id"] for row in all_traces]
        spans: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []
        span_id_maps: dict[str, dict[str, str]] = {}
        for trace_id in trace_ids:
            trace_spans = await self._store.list_spans(trace_id)
            span_id_maps[trace_id] = self._span_identity_map(trace_spans)
            spans.extend(trace_spans)
            events.extend(await self._store.list_events(trace_id))
        spans.sort(key=lambda row: (row.get("sequence_no") or 0, _iso(row.get("started_at")) or ""))
        return {
            "session_id": session_id,
            "traces": [
                self._trace_dto(row, span_id_maps.get(row["trace_id"]))
                for row in all_traces
            ],
            "spans": [
                self._span_dto(row, span_id_maps.get(row["trace_id"])) for row in spans
            ],
            "events": [
                self._event_dto(row, span_id_maps.get(row["trace_id"]))
                for row in events
            ],
        }

    async def feedback_context(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        before: datetime,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Traces of a session that started at or before the feedback time
        (design 12.4): later traces must not enter the feedback context.

        Entries are shaped like the legacy ``TraceRecord``/``TraceEvent``/
        ``TraceSpan`` models (``StrictModel`` with ``extra="forbid"``) so the
        admin feedback page can keep rendering them.
        """
        if self._store is None:
            return []
        traces = await self._store.list_traces(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            started_before=before + timedelta(milliseconds=1),
            limit=max(1, min(limit, 200)),
        )
        result: list[dict[str, Any]] = []
        for row in traces:
            spans, events = await asyncio.gather(
                self._store.list_spans(row["trace_id"]),
                self._store.list_events(row["trace_id"]),
            )
            span_id_map = self._span_identity_map(spans)
            root_span_id = row.get("root_span_id")
            result.append(
                {
                    "trace_id": row.get("legacy_trace_id") or row["trace_id"],
                    "root_span_id": self._display_span_id(root_span_id, span_id_map),
                    "conversation_id": row.get("session_id"),
                    "user_id": row["user_id"],
                    "tenant_id": row["tenant_id"],
                    "status": row["status"],
                    "created_at": _iso(row.get("started_at")) or datetime.now(timezone.utc).isoformat(),
                    "completed_at": _iso(row.get("completed_at")),
                    "spans": [self._legacy_span_dict(span, span_id_map) for span in spans],
                    "events": [self._legacy_event_dict(event) for event in events],
                }
            )
        return result

    @staticmethod
    def _legacy_span_dict(
        span: dict[str, Any],
        span_id_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        kind = span.get("kind")
        if kind not in ("agent", "model", "tool", "aina", "internal"):
            kind = "internal"
        status = span.get("status")
        if status not in ("running", "completed", "failed", "cancelled", "approval_required"):
            status = "completed"
        parent_span_id = span.get("parent_span_id")
        return {
            "span_id": span.get("legacy_span_id") or span["span_id"],
            "parent_span_id": ObsQueryService._display_span_id(
                parent_span_id,
                span_id_map,
            ),
            "kind": kind,
            "name": span.get("name") or "",
            "status": status,
            "target_id": span.get("target_id"),
            "target_version": span.get("target_version"),
            "logical_call_id": None,
            "attempt_no": 1,
            "started_at": _iso(span.get("started_at")) or datetime.now(timezone.utc).isoformat(),
            "first_output_at": _iso(span.get("first_output_at")),
            "completed_at": _iso(span.get("completed_at")),
            "duration_ms": span.get("duration_ms"),
            "input": _preview_json(span.get("input_preview")),
            "output": _preview_json(span.get("output_preview")),
            "attributes": span.get("attributes") or {},
            "error": span.get("error"),
        }

    @staticmethod
    def _legacy_event_dict(event: dict[str, Any]) -> dict[str, Any]:
        return {
            "timestamp": _iso(event.get("occurred_at")) or datetime.now(timezone.utc).isoformat(),
            "kind": event.get("name") or "event",
            "status": event.get("status") or "completed",
            "target_type": None,
            "target_id": None,
            "duration_ms": None,
            "details": event.get("attributes") or {},
        }

    # ---- DTO helpers ----

    @staticmethod
    def _empty_overview(range_name: str) -> dict[str, Any]:
        return {
            "range": range_name,
            "trace_count": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "total_tokens": 0,
            "error_count": 0,
            "active_days": 0,
            "conversation_count": 0,
            "per_model": [],
            "daily": [],
        }

    @staticmethod
    def _span_identity_map(spans: list[dict[str, Any]]) -> dict[str, str]:
        return {
            str(span["span_id"]): str(span.get("legacy_span_id") or span["span_id"])
            for span in spans
        }

    @staticmethod
    def _display_span_id(
        span_id: Any,
        span_id_map: dict[str, str] | None,
    ) -> str | None:
        if span_id is None:
            return None
        raw_span_id = str(span_id)
        return (span_id_map or {}).get(raw_span_id, raw_span_id)

    @staticmethod
    def _trace_dto(
        row: dict[str, Any],
        span_id_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        root_span_id = row.get("root_span_id")
        return {
            "trace_id": row["trace_id"],
            "legacy_trace_id": row.get("legacy_trace_id"),
            "root_span_id": ObsQueryService._display_span_id(root_span_id, span_id_map),
            "session_id": row.get("session_id"),
            "user_id": row["user_id"],
            "tenant_id": row["tenant_id"],
            "status": row["status"],
            "started_at": _iso(row.get("started_at")),
            "completed_at": _iso(row.get("completed_at")),
            "duration_ms": row.get("duration_ms"),
            "input_tokens": int(row.get("input_tokens") or 0),
            "output_tokens": int(row.get("output_tokens") or 0),
            "cache_read_tokens": int(row.get("cache_read_tokens") or 0),
            "message_count": int(row.get("message_count") or 0),
            "compression_count": int(row.get("compression_count") or 0),
            "error_count": int(row.get("error_count") or 0),
            "attributes": row.get("attributes") or {},
        }

    @staticmethod
    def _span_dto(
        row: dict[str, Any],
        span_id_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        input_preview = _preview_json(row.get("input_preview"))
        output_preview = _preview_json(row.get("output_preview"))
        input_tokens, output_tokens, attributes = _span_usage(row, input_preview, output_preview)
        parent_otel_span_id = row.get("parent_span_id")
        return {
            "span_id": row.get("legacy_span_id") or row["span_id"],
            "otel_span_id": row["span_id"],
            "trace_id": row["trace_id"],
            "parent_span_id": ObsQueryService._display_span_id(
                parent_otel_span_id,
                span_id_map,
            ),
            "parent_otel_span_id": parent_otel_span_id,
            "sequence_no": int(row.get("sequence_no") or 0),
            "kind": row["kind"],
            "name": row["name"],
            "target_id": row.get("target_id"),
            "model": row.get("model"),
            "status": row["status"],
            "started_at": _iso(row.get("started_at")),
            "first_output_at": _iso(row.get("first_output_at")),
            "completed_at": _iso(row.get("completed_at")),
            "duration_ms": row.get("duration_ms"),
            "ttft_ms": row.get("ttft_ms"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": int(row.get("cache_read_tokens") or 0),
            "input": input_preview,
            "output": output_preview,
            "attributes": attributes,
            "error": row.get("error"),
            "raw_io_path": row.get("raw_io_path"),
            "raw_io_status": row.get("raw_io_status"),
        }

    @staticmethod
    def _event_dto(
        row: dict[str, Any],
        span_id_map: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        otel_span_id = row.get("span_id")
        return {
            "event_id": row["event_id"],
            "trace_id": row["trace_id"],
            "span_id": ObsQueryService._display_span_id(otel_span_id, span_id_map),
            "otel_span_id": otel_span_id,
            "name": row["name"],
            "status": row.get("status"),
            "occurred_at": _iso(row.get("occurred_at")),
            "attributes": row.get("attributes") or {},
        }
