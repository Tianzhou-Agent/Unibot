import gzip
import hashlib
import json
from datetime import datetime, timezone

import pytest

from tianzhou_agent_platform.core.context_compression import estimate_request_tokens
from tianzhou_agent_platform.core.observability_query import ObsQueryService


def _model_span_row(**overrides):
    row = {
        "span_id": "otel-span",
        "legacy_span_id": "span-model",
        "trace_id": "trace-1",
        "parent_span_id": "span-root",
        "sequence_no": 1,
        "kind": "model",
        "name": "chat.completions",
        "target_id": None,
        "model": "test-model",
        "status": "completed",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "input_preview": '{"messages":[{"role":"user","content":"hello"}],"estimated_prompt_tokens":17}',
        "output_preview": '{"role":"assistant","content":"hello back"}',
        "attributes": {"provider": "test"},
    }
    row.update(overrides)
    return row


def test_span_dto_estimates_historical_missing_model_usage() -> None:
    dto = ObsQueryService._span_dto(_model_span_row())

    assert dto["input_tokens"] == 17
    assert dto["output_tokens"] == estimate_request_tokens([dto["output"]])
    assert dto["attributes"] == {
        "provider": "test",
        "usage_estimated": True,
        "usage_source": "estimated",
    }


def test_span_dto_preserves_reported_usage() -> None:
    dto = ObsQueryService._span_dto(_model_span_row(input_tokens=10, output_tokens=5))

    assert dto["input_tokens"] == 10
    assert dto["output_tokens"] == 5
    assert dto["attributes"] == {"provider": "test"}


class _SessionStore:
    def __init__(self) -> None:
        self.trace = {
            "trace_id": "0123456789abcdef0123456789abcdef",
            "legacy_trace_id": "trace_0123456789abcdef0123456789abcdef",
            "root_span_id": "1111111111111111",
            "session_id": "conv_1",
            "user_id": "user_1",
            "tenant_id": "tenant_1",
            "status": "completed",
            "started_at": datetime.now(timezone.utc),
        }
        self.spans = [
            {
                **_model_span_row(
                    span_id="1111111111111111",
                    legacy_span_id="span_root",
                    trace_id=self.trace["trace_id"],
                    parent_span_id=None,
                    kind="agent",
                ),
                "name": "agent.run",
            },
            _model_span_row(
                span_id="2222222222222222",
                legacy_span_id="span_model",
                trace_id=self.trace["trace_id"],
                parent_span_id="1111111111111111",
            ),
        ]

    async def list_traces(self, **_: object):
        return [self.trace]

    async def list_spans(self, trace_id: str):
        return self.spans if trace_id == self.trace["trace_id"] else []

    async def list_events(self, trace_id: str):
        if trace_id != self.trace["trace_id"]:
            return []
        return [
            {
                "event_id": "event_1",
                "trace_id": trace_id,
                "span_id": "2222222222222222",
                "name": "model.completed",
                "status": "completed",
                "occurred_at": datetime.now(timezone.utc),
                "attributes": {},
            }
        ]


@pytest.mark.asyncio
async def test_session_and_feedback_dtos_use_one_legacy_span_id_namespace() -> None:
    store = _SessionStore()
    query = ObsQueryService(store, None)  # type: ignore[arg-type]

    session = await query.session_detail(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="conv_1",
    )
    assert session is not None
    assert session["traces"][0]["trace_id"] == store.trace["trace_id"]
    assert session["traces"][0]["root_span_id"] == "span_root"
    child = next(span for span in session["spans"] if span["span_id"] == "span_model")
    assert child["parent_span_id"] == "span_root"
    assert child["otel_span_id"] == "2222222222222222"
    assert child["parent_otel_span_id"] == "1111111111111111"
    assert session["events"][0]["span_id"] == "span_model"
    assert session["events"][0]["otel_span_id"] == "2222222222222222"

    feedback = await query.feedback_context(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="conv_1",
        before=datetime.now(timezone.utc),
    )
    assert feedback[0]["trace_id"] == store.trace["legacy_trace_id"]
    assert feedback[0]["root_span_id"] == "span_root"
    feedback_child = next(span for span in feedback[0]["spans"] if span["span_id"] == "span_model")
    assert feedback_child["parent_span_id"] == "span_root"


class _RawLogStore:
    def __init__(self, row):
        self._row = row

    async def get_span(self, span_id: str):
        return self._row if span_id == self._row["span_id"] else None


@pytest.mark.asyncio
async def test_raw_log_rejects_content_with_wrong_checksum(tmp_path) -> None:
    content = gzip.compress(json.dumps({"request": "complete"}).encode())
    raw_path = tmp_path / "trace" / "span.json.gz"
    raw_path.parent.mkdir()
    raw_path.write_bytes(content)
    store = _RawLogStore(
        {
            "span_id": "span-1",
            "trace_id": "trace-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "raw_io_path": "trace/span.json.gz",
            "raw_io_status": "ready",
            "raw_io_size_bytes": len(content),
            "raw_io_sha256": "0" * 64,
        }
    )

    result = await ObsQueryService(store, tmp_path).raw_log(
        tenant_id="tenant-1",
        user_id="user-1",
        trace_id="trace-1",
        span_id="span-1",
    )

    assert result == {"status": "failed", "detail": None}


@pytest.mark.asyncio
async def test_raw_log_serves_content_with_matching_checksum(tmp_path) -> None:
    document = {"request": "complete"}
    content = gzip.compress(json.dumps(document).encode())
    raw_path = tmp_path / "trace" / "span.json.gz"
    raw_path.parent.mkdir()
    raw_path.write_bytes(content)
    store = _RawLogStore(
        {
            "span_id": "span-1",
            "trace_id": "trace-1",
            "tenant_id": "tenant-1",
            "user_id": "user-1",
            "raw_io_path": "trace/span.json.gz",
            "raw_io_status": "ready",
            "raw_io_size_bytes": len(content),
            "raw_io_sha256": hashlib.sha256(content).hexdigest(),
        }
    )

    result = await ObsQueryService(store, tmp_path).raw_log(
        tenant_id="tenant-1",
        user_id="user-1",
        trace_id="trace-1",
        span_id="span-1",
    )

    assert result == {"status": "ready", "detail": document}
