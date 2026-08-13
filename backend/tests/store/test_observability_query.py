"""ObsQueryService tests: aggregation DTOs, feedback-context time filter and
permission enforcement (raw IO ownership, tenant/user scoping). Uses a real
MySQL when reachable (skip otherwise), like the store tests.
"""

from __future__ import annotations

import gzip
import json
import os
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tianzhou_agent_platform.core.observability_query import MAX_RAW_LOG_BYTES, ObsQueryService, range_bounds
from tianzhou_agent_platform.store.observability_raw import RawIoWriter
from tianzhou_agent_platform.store.observability_store import (
    EVENTS_TABLE,
    OBS_METADATA,
    SPANS_TABLE,
    TRACES_TABLE,
    ObservabilityStore,
)
from tianzhou_agent_platform.store.observability_wal import ObsRecord

DSN = os.getenv("OBS_TEST_MYSQL_DSN", "")


def _can_connect() -> bool:
    if not DSN:
        return False
    try:
        host, port = DSN.split("@")[1].split("/")[0].split(":")
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _can_connect(), reason="MySQL is not reachable for OBS query tests")


def make_trace(
    sequence_no: int,
    trace_id: str,
    *,
    user_id: str = "user_1",
    session_id: str = "conv_1",
    started_at: str = "2026-08-06T10:00:00Z",
    status: str = "completed",
    input_tokens: int = 100,
    output_tokens: int = 50,
) -> ObsRecord:
    return ObsRecord(
        record_type="trace_finished",
        producer_instance_id="node-1-abc",
        sequence_no=sequence_no,
        trace_id=trace_id,
        payload={
            "legacy_trace_id": f"trace_{trace_id[-8:]}",
            "root_span_id": "span_root",
            "session_id": session_id,
            "user_id": user_id,
            "tenant_id": "tenant_1",
            "status": status,
            "started_at": started_at,
            "completed_at": "2026-08-06T10:00:05Z",
            "duration_ms": 5000.0,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": 10,
            "message_count": 2,
            "compression_count": 1,
            "error_count": 0,
        },
    )


def make_span(sequence_no: int, trace_id: str, span_id: str, *, kind: str = "model", model: str = "gpt-test") -> ObsRecord:
    return ObsRecord(
        record_type="span_finished",
        producer_instance_id="node-1-abc",
        sequence_no=sequence_no,
        trace_id=trace_id,
        span_id=span_id,
        payload={
            "legacy_span_id": f"span_{span_id[-8:]}",
            "parent_span_id": "span_root",
            "sequence_no": sequence_no,
            "session_id": "conv_1",
            "user_id": "user_1",
            "tenant_id": "tenant_1",
            "kind": kind,
            "name": "chat.completions",
            "model": model,
            "status": "completed",
            "started_at": "2026-08-06T10:00:01Z",
            "completed_at": "2026-08-06T10:00:03Z",
            "duration_ms": 2000.0,
            "ttft_ms": 150.0,
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "input_preview": '{"messages":[{"role":"user","content":"hello"}]}',
            "output_preview": '{"content":"hi"}',
            "raw_io_path": f"tenant_1/user_1/{trace_id}/{span_id}.json.gz",
            "raw_io_sha256": "a" * 64,
            "raw_io_size_bytes": 123,
            "raw_io_status": "ready",
        },
    )


@pytest.fixture
async def obs_ctx(tmp_path: Path):
    store = ObservabilityStore.from_dsn(DSN)
    async with store._engine.begin() as connection:  # noqa: SLF001
        await connection.run_sync(OBS_METADATA.drop_all)
        await connection.run_sync(OBS_METADATA.create_all)
    raw_root = tmp_path / "raw"
    raw_root.mkdir(parents=True)
    query = ObsQueryService(store, raw_root)
    yield {"store": store, "query": query, "raw_root": raw_root}
    await store.close()


async def _seed(store: ObservabilityStore) -> None:
    now = datetime.now(timezone.utc)
    day = lambda n: (now - timedelta(days=n)).isoformat()  # noqa: E731
    await store.bulk_upsert(
        [
            make_trace(1, "trace_aaa", started_at=day(1)),
            make_trace(2, "trace_bbb", started_at=(now - timedelta(hours=6)).isoformat(), input_tokens=250),
            make_trace(3, "trace_ccc", user_id="user_2", session_id="conv_ccc", started_at=day(3), input_tokens=999),
            make_trace(4, "trace_ddd", session_id="conv_other", started_at=day(1), input_tokens=5),
            make_span(5, "trace_aaa", "span_aaa"),
            make_span(6, "trace_bbb", "span_bbb", model="gpt-other"),
        ]
    )


async def test_personal_overview_scoped_to_own_data(obs_ctx) -> None:
    await _seed(obs_ctx["store"])
    overview = await obs_ctx["query"].personal_overview(
        tenant_id="tenant_1",
        user_id="user_1",
        range_name="week",
    )
    assert overview["range"] == "week"
    # user_2's trace is excluded; conv_other belongs to user_1 but is a
    # different conversation (still counted in the personal overview)
    assert overview["trace_count"] == 3
    assert overview["input_tokens"] == 355
    assert overview["output_tokens"] == 150
    assert overview["cache_read_tokens"] == 30
    # total excludes cache_read (subset of input tokens)
    assert overview["total_tokens"] == 505
    assert overview["active_days"] >= 1
    models = {row["model"]: row for row in overview["per_model"]}
    assert models["gpt-test"]["call_count"] == 1
    assert models["gpt-other"]["call_count"] == 1


async def test_session_detail_returns_tree_ready_dto(obs_ctx) -> None:
    await _seed(obs_ctx["store"])
    detail = await obs_ctx["query"].session_detail(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="conv_1",
    )
    assert detail is not None
    assert len(detail["traces"]) == 2
    assert len(detail["spans"]) == 2
    span = next(s for s in detail["spans"] if s["name"] == "chat.completions")
    assert span["input"] == {"messages": [{"role": "user", "content": "hello"}]}
    assert span["output"] == {"content": "hi"}
    assert span["ttft_ms"] == 150.0


async def test_session_detail_denied_for_other_user(obs_ctx) -> None:
    await _seed(obs_ctx["store"])
    detail = await obs_ctx["query"].session_detail(
        tenant_id="tenant_1",
        user_id="user_2",
        session_id="conv_1",
    )
    assert detail is None  # conv_1 belongs to user_1


async def test_raw_log_ownership_enforced(obs_ctx) -> None:
    await _seed(obs_ctx["store"])
    raw_root = obs_ctx["raw_root"]
    payload = gzip.compress(json.dumps({"kind": "model", "request": {"model": "gpt"}}).encode())
    target = raw_root / "tenant_1" / "user_1" / "trace_aaa" / "span_aaa.json.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)

    # wrong user cannot read
    denied = await obs_ctx["query"].raw_log(
        tenant_id="tenant_1",
        user_id="user_2",
        trace_id="trace_aaa",
        span_id="span_aaa",
    )
    assert denied is None
    # owning user can read
    granted = await obs_ctx["query"].raw_log(
        tenant_id="tenant_1",
        user_id="user_1",
        trace_id="trace_aaa",
        span_id="span_aaa",
    )
    assert granted is not None
    assert granted["status"] == "ready"
    assert granted["detail"]["kind"] == "model"


async def test_feedback_context_excludes_later_traces(obs_ctx) -> None:
    await _seed(obs_ctx["store"])
    now = datetime.now(timezone.utc)
    # feedback happened after trace_aaa started (1d ago) but before trace_bbb started (6h ago)
    feedback_time = now - timedelta(hours=12)
    context = await obs_ctx["query"].feedback_context(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="conv_1",
        before=feedback_time,
    )
    assert len(context) == 1
    assert context[0]["trace_id"] == "trace_aaa"
    assert "spans" in context[0] and "events" in context[0]


async def test_feedback_context_entries_validate_as_trace_record(obs_ctx) -> None:
    """Feedback context entries must pass TraceRecord strict validation so the
    admin feedback page (FeedbackDetail.context_traces) never 500s."""
    from tianzhou_agent_platform.core.chat import TraceEvent, TraceRecord, TraceSpan

    await _seed(obs_ctx["store"])
    now = datetime.now(timezone.utc)
    feedback_time = now - timedelta(hours=12)
    context = await obs_ctx["query"].feedback_context(
        tenant_id="tenant_1",
        user_id="user_1",
        session_id="conv_1",
        before=feedback_time,
    )
    assert context, "expected at least one context trace"
    for entry in context:
        trace = TraceRecord.model_validate(entry)
        assert trace.trace_id
        for span_dict in entry["spans"]:
            TraceSpan.model_validate(span_dict)
        for event_dict in entry["events"]:
            TraceEvent.model_validate(event_dict)


async def test_raw_log_zip_bomb_rejected(obs_ctx) -> None:
    """Decompression is bounded: a gzip stream that expands beyond the limit
    must be rejected instead of exhausting memory."""
    import gzip as gzip_mod

    from tianzhou_agent_platform.core.observability_query import _bounded_gzip_decompress

    bomb = gzip_mod.compress(b"x" * (MAX_RAW_LOG_BYTES + 1024))
    with pytest.raises(ValueError):
        _bounded_gzip_decompress(bomb, MAX_RAW_LOG_BYTES)
    # truncated stream must also be rejected
    truncated = gzip_mod.compress(b"payload")[:-5]
    with pytest.raises(ValueError):
        _bounded_gzip_decompress(truncated, MAX_RAW_LOG_BYTES)


async def test_raw_log_too_large_returns_status(obs_ctx) -> None:
    await _seed(obs_ctx["store"])
    raw_root = obs_ctx["raw_root"]
    payload = gzip.compress(json.dumps({"kind": "model", "big": "x" * 1024}).encode())
    target = raw_root / "tenant_1" / "user_1" / "trace_aaa" / "span_aaa.json.gz"
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    # inflate the recorded size beyond the limit -> too_large without reading
    span = await obs_ctx["store"].get_span("span_aaa")
    assert span is not None
    await obs_ctx["store"].bulk_upsert(
        [
            ObsRecord(
                record_type="span_finished",
                producer_instance_id="node-1-abc",
                sequence_no=99,
                trace_id="trace_aaa",
                span_id="span_aaa",
                payload={
                    **span,
                    "raw_io_size_bytes": MAX_RAW_LOG_BYTES + 1,
                },
            )
        ]
    )
    result = await obs_ctx["query"].raw_log(
        tenant_id="tenant_1",
        user_id="user_1",
        trace_id="trace_aaa",
        span_id="span_aaa",
    )
    assert result is not None
    assert result["status"] == "too_large"


def test_range_bounds_day_week_month() -> None:
    now = datetime(2026, 8, 8, 12, 0, tzinfo=timezone.utc)
    day_start, day_end = range_bounds("day", now=now)
    assert day_start.day == 8
    week_start, _ = range_bounds("week", now=now)
    assert (now - week_start).days == 6
    month_start, _ = range_bounds("month", now=now)
    assert month_start.day == 10  # 30 days back


async def test_query_service_degrades_when_disabled(tmp_path: Path) -> None:
    query = ObsQueryService(None, None)
    overview = await query.personal_overview(tenant_id="t", user_id="u")
    assert overview["trace_count"] == 0
    detail = await query.session_detail(tenant_id="t", user_id="u", session_id="s")
    assert detail is None
