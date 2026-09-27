"""OpenTelemetry TracerProvider and durable OBS span processing.

Design (ir-01 section 4.2, 5, 6): the OTel SDK is used as the in-process
Trace/Span data model; ``DurableBufferSpanProcessor.on_end`` converts each ended
span into an immutable ``ObsRecord`` and submits it to the buffer synchronously
(``SpanProcessor.on_end`` has no await). Trace start/terminal records remain
owned by the agent-run observer because they carry business aggregates and
approval checkpoint semantics that cannot be inferred from a generic span.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult  # noqa: F401 - interface parity
from opentelemetry.trace import StatusCode, get_tracer_provider, set_tracer_provider

from tianzhou_agent_platform.core.observation_context import is_observation_suppressed
from tianzhou_agent_platform.store.observability_buffer import (
    DurableObsBuffer,
    ObsBufferError,
    ObsRecord,
)

logger = logging.getLogger(__name__)

SERVICE_NAME = "unibot-backend"
SCHEMA_VERSION = 1

# attribute keys set by ObservabilityAspect on OTel spans
ATTR_SPAN_ROLE = "unibot.span_role"  # "root" | "child"
ATTR_LEGACY_TRACE_ID = "unibot.trace_id"
ATTR_LEGACY_SPAN_ID = "unibot.span_id"
ATTR_LEGACY_PARENT_SPAN_ID = "unibot.parent_span_id"
ATTR_SPAN_KIND = "unibot.span_kind"  # agent|model|tool|aina|internal
ATTR_SEQUENCE_NO = "unibot.sequence_no"
ATTR_SESSION_ID = "session.id"
ATTR_USER_ID = "user.id"
ATTR_TENANT_ID = "unibot.tenant.id"
ATTR_CONVERSATION_ID = "gen_ai.conversation.id"
ATTR_OPERATION_NAME = "gen_ai.operation.name"
ATTR_REQUEST_MODEL = "gen_ai.request.model"
ATTR_RESPONSE_MODEL = "gen_ai.response.model"
ATTR_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ATTR_CACHE_READ_TOKENS = "gen_ai.usage.cache_read.input_tokens"
ATTR_TTFT_MS = "unibot.gen_ai.ttft_ms"
ATTR_TARGET_ID = "unibot.target_id"
ATTR_TARGET_VERSION = "unibot.target_version"
ATTR_FIRST_OUTPUT_AT = "unibot.first_output_at"
ATTR_INPUT_PREVIEW = "unibot.input_preview"
ATTR_OUTPUT_PREVIEW = "unibot.output_preview"
ATTR_ERROR_JSON = "unibot.error.json"
ATTR_RAW_IO_PATH = "unibot.raw_io.path"
ATTR_RAW_IO_SHA256 = "unibot.raw_io.sha256"
ATTR_RAW_IO_SIZE = "unibot.raw_io.size_bytes"
ATTR_RAW_IO_STATUS = "unibot.raw_io.status"
ATTR_TRACE_STATUS = "unibot.trace_status"
ATTR_MESSAGE_COUNT = "unibot.message_count"
ATTR_COMPRESSION_COUNT = "unibot.compression_count"
ATTR_ERROR_COUNT = "unibot.error_count"
ATTR_OBSERVATION_SUPPRESSED = "unibot.observation.suppressed"

# statuses reported by the business layer through unibot.status
UNIBOT_STATUS_ATTR = "unibot.status"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_APPROVAL_REQUIRED = "approval_required"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

_EXTRA_ATTRIBUTE_KEYS = {
    ATTR_SPAN_ROLE,
    ATTR_LEGACY_TRACE_ID,
    ATTR_LEGACY_SPAN_ID,
    ATTR_LEGACY_PARENT_SPAN_ID,
    ATTR_SPAN_KIND,
    ATTR_SEQUENCE_NO,
    ATTR_SESSION_ID,
    ATTR_USER_ID,
    ATTR_TENANT_ID,
    ATTR_CONVERSATION_ID,
    ATTR_OPERATION_NAME,
    ATTR_REQUEST_MODEL,
    ATTR_RESPONSE_MODEL,
    ATTR_INPUT_TOKENS,
    ATTR_OUTPUT_TOKENS,
    ATTR_CACHE_READ_TOKENS,
    ATTR_TTFT_MS,
    ATTR_TARGET_ID,
    ATTR_TARGET_VERSION,
    ATTR_FIRST_OUTPUT_AT,
    ATTR_INPUT_PREVIEW,
    ATTR_OUTPUT_PREVIEW,
    ATTR_ERROR_JSON,
    ATTR_RAW_IO_PATH,
    ATTR_RAW_IO_SHA256,
    ATTR_RAW_IO_SIZE,
    ATTR_RAW_IO_STATUS,
    ATTR_TRACE_STATUS,
    ATTR_MESSAGE_COUNT,
    ATTR_COMPRESSION_COUNT,
    ATTR_ERROR_COUNT,
    ATTR_OBSERVATION_SUPPRESSED,
    UNIBOT_STATUS_ATTR,
}


def _as_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_string(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, default=str)


def otel_status_to_unibot(status: Any, business_status: str | None) -> str:
    """Map OTel StatusCode to the Unibot page status (design 5.5)."""
    if business_status is not None:
        return business_status
    if status is not None and status.status_code == StatusCode.ERROR:
        return STATUS_FAILED
    return STATUS_COMPLETED


def record_from_span(span: ReadableSpan, producer_instance_id: str, sequence_no: int) -> ObsRecord:
    """Convert one ended OTel span into its finished durable record."""
    attributes = dict(span.attributes or {})
    raw_business_status = attributes.get(UNIBOT_STATUS_ATTR)
    business_status = raw_business_status if isinstance(raw_business_status, str) else None
    unibot_status = otel_status_to_unibot(span.status, business_status)
    started_at = span.start_time
    ended_at = span.end_time
    started = datetime.fromtimestamp(started_at / 1e9, tz=timezone.utc) if started_at else datetime.now(timezone.utc)
    ended = datetime.fromtimestamp(ended_at / 1e9, tz=timezone.utc) if ended_at else None
    duration_ms = (ended_at - started_at) / 1e6 if ended_at and started_at else None
    raw_error_json = attributes.get(ATTR_ERROR_JSON)

    span_payload: dict[str, Any] = {
        "legacy_span_id": attributes.get(ATTR_LEGACY_SPAN_ID),
        # parent is the OTel parent SpanContext (8-byte span id -> 16 hex);
        # the legacy parent id lives in attributes for reference only and
        # must not be stored in the VARCHAR(32) parent_span_id column.
        # The root span's synthetic parent (span_id=1) is not a real span,
        # so root records keep parent_span_id=NULL.
        "parent_span_id": (
            None
            if attributes.get(ATTR_SPAN_ROLE) == "root" or span.parent is None
            else span.parent.span_id.to_bytes(8, "big").hex()
        ),
        "sequence_no": attributes.get(ATTR_SEQUENCE_NO) or 0,
        "session_id": attributes.get(ATTR_SESSION_ID) or attributes.get(ATTR_CONVERSATION_ID),
        "user_id": attributes.get(ATTR_USER_ID) or "anonymous",
        "tenant_id": attributes.get(ATTR_TENANT_ID) or "default",
        "kind": attributes.get(ATTR_SPAN_KIND) or "internal",
        "name": span.name,
        "target_id": attributes.get(ATTR_TARGET_ID),
        "target_version": attributes.get(ATTR_TARGET_VERSION),
        "model": attributes.get(ATTR_RESPONSE_MODEL) or attributes.get(ATTR_REQUEST_MODEL),
        "status": unibot_status,
        "started_at": started.isoformat(),
        "first_output_at": attributes.get(ATTR_FIRST_OUTPUT_AT),
        "completed_at": ended.isoformat() if ended else None,
        "duration_ms": duration_ms,
        "ttft_ms": attributes.get(ATTR_TTFT_MS),
        "input_tokens": attributes.get(ATTR_INPUT_TOKENS) or 0,
        "output_tokens": attributes.get(ATTR_OUTPUT_TOKENS) or 0,
        "cache_read_tokens": attributes.get(ATTR_CACHE_READ_TOKENS) or 0,
        "input_preview": attributes.get(ATTR_INPUT_PREVIEW),
        "output_preview": attributes.get(ATTR_OUTPUT_PREVIEW),
        "attributes": {
            key: value for key, value in attributes.items() if key not in _EXTRA_ATTRIBUTE_KEYS
        },
        "error": (
            json.loads(raw_error_json) if isinstance(raw_error_json, str) else None
        ),
        "raw_io_path": attributes.get(ATTR_RAW_IO_PATH),
        "raw_io_sha256": attributes.get(ATTR_RAW_IO_SHA256),
        "raw_io_size_bytes": attributes.get(ATTR_RAW_IO_SIZE),
        "raw_io_status": attributes.get(ATTR_RAW_IO_STATUS) or "not_applicable",
    }

    record = ObsRecord(
        record_type="span_finished",
        producer_instance_id=producer_instance_id,
        sequence_no=sequence_no,
        trace_id=span.context.trace_id.to_bytes(16, "big").hex(),
        span_id=span.context.span_id.to_bytes(8, "big").hex(),
        occurred_at=ended or started,
        payload=span_payload,
    )
    return record


class DurableBufferSpanProcessor(SpanProcessor):
    """Convert ended OTel spans into immutable buffered records."""

    def __init__(self, buffer: DurableObsBuffer) -> None:
        self._buffer = buffer

    def on_start(self, span: Any, parent_context: Any | None = None) -> None:  # noqa: ANN401
        if is_observation_suppressed():
            span.set_attribute(ATTR_OBSERVATION_SUPPRESSED, True)

    def on_end(self, span: ReadableSpan) -> None:
        if (span.attributes or {}).get(ATTR_OBSERVATION_SUPPRESSED):
            return
        try:
            record = record_from_span(span, self._buffer.producer_instance_id, 0)
            self._buffer.submit(record)
        except ObsBufferError:
            logger.exception("OBS buffer submit failed for ended span %s", span.name)
        except Exception:  # noqa: BLE001 - observability must never break business
            logger.exception("span -> OBS record conversion failed for span %s", span.name)

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


DurableWalSpanProcessor = DurableBufferSpanProcessor


def setup_tracer_provider(
    processor: DurableBufferSpanProcessor,
    *,
    service_name: str = SERVICE_NAME,
    service_instance_id: str | None = None,
) -> TracerProvider:
    """Create and install the application TracerProvider (design 16.1 step 8)."""
    resource_attributes: dict[str, Any] = {"service.name": service_name}
    if service_instance_id:
        resource_attributes["service.instance.id"] = service_instance_id
    provider = TracerProvider(resource=Resource.create(resource_attributes))
    provider.add_span_processor(processor)
    set_tracer_provider(provider)
    return provider


def shutdown_tracer_provider() -> None:
    try:
        provider = get_tracer_provider()
        if isinstance(provider, TracerProvider):
            provider.shutdown()
    except Exception:  # noqa: BLE001
        logger.exception("OTel tracer provider shutdown failed")


def noop_exporter_factory() -> SpanExporter:  # pragma: no cover - interface marker
    """Placeholder explaining why no OTLP exporter is installed this phase.

    The design keeps the OTel SDK in-process and replaces the exporter with
    the DurableBufferSpanProcessor; no Collector/OTLP is deployed.
    """

    class _Noop(SpanExporter):
        def export(self, spans: Any) -> SpanExportResult:  # noqa: ANN401
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

    return _Noop()
