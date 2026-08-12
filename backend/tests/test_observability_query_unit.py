import gzip
import hashlib
import json

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
