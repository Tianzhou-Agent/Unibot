"""ObservabilityStore tests: schema, idempotent bulk UPSERT against a real
MySQL only when OBS_TEST_MYSQL_DSN explicitly points at an isolated test
database; tests are skipped otherwise so a running development DB is never
recreated by the fixtures.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select, text

from tianzhou_agent_platform.store.observability_store import (
    EVENTS_TABLE,
    OBS_METADATA,
    OPS_FIRST_USE_TABLE,
    OPS_REQUEST_AGENTS_TABLE,
    OPS_USER_EVENTS_TABLE,
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
        import socket

        host, port = DSN.split("@")[1].split("/")[0].split(":")
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _can_connect(), reason="MySQL is not reachable for OBS store tests")


def make_trace_finished(sequence_no: int, trace_id: str, *, user_id: str = "user_1", input_tokens: int = 100) -> ObsRecord:
    return ObsRecord(
        record_type="trace_finished",
        producer_instance_id="node-1-abc",
        sequence_no=sequence_no,
        trace_id=trace_id,
        payload={
            "legacy_trace_id": f"trace_{trace_id[-6:]}",
            "root_span_id": "span_root",
            "session_id": "conv_1",
            "user_id": user_id,
            "tenant_id": "tenant_1",
            "status": "completed",
            "started_at": "2026-08-06T10:00:00Z",
            "completed_at": "2026-08-06T10:00:05Z",
            "duration_ms": 5000.0,
            "input_tokens": input_tokens,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "message_count": 3,
            "compression_count": 1,
            "error_count": 0,
            "attributes": {"source": "test"},
        },
    )


def make_span_finished(
    sequence_no: int,
    trace_id: str,
    span_id: str,
    *,
    kind: str = "model",
    target_id: str = "model-prod",
    target_version: str | None = None,
) -> ObsRecord:
    return ObsRecord(
        record_type="span_finished",
        producer_instance_id="node-1-abc",
        sequence_no=sequence_no,
        trace_id=trace_id,
        span_id=span_id,
        payload={
            "legacy_span_id": f"span_{span_id[-6:]}",
            "parent_span_id": "span_root",
            "sequence_no": sequence_no,
            "session_id": "conv_1",
            "user_id": "user_1",
            "tenant_id": "tenant_1",
            "kind": kind,
            "name": "chat.completions",
            "target_id": target_id,
            "target_version": target_version,
            "model": "gpt-test",
            "status": "completed",
            "started_at": "2026-08-06T10:00:01Z",
            "completed_at": "2026-08-06T10:00:03Z",
            "duration_ms": 2000.0,
            "ttft_ms": 150.0,
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "input_preview": "hello",
            "output_preview": "hi",
            "attributes": {"gen_ai.operation.name": "chat"},
            "error": None,
            "raw_io_path": "tenant_1/user_1/trace_x/span_y.json.gz",
            "raw_io_sha256": "a" * 64,
            "raw_io_size_bytes": 123,
            "raw_io_status": "ready",
        },
    )


def make_event(
    sequence_no: int,
    trace_id: str,
    *,
    name: str = "approval.requested",
    occurred_at: str = "2026-08-06T10:00:02Z",
) -> ObsRecord:
    return ObsRecord(
        record_type="event",
        producer_instance_id="node-1-abc",
        sequence_no=sequence_no,
        occurred_at=datetime.fromisoformat(occurred_at.replace("Z", "+00:00")),
        trace_id=trace_id,
        span_id="span_root",
        payload={
            "session_id": "conv_1",
            "user_id": "user_1",
            "tenant_id": "tenant_1",
            "name": name,
            "status": "pending",
            "occurred_at": occurred_at,
            "attributes": {"approval_id": "appr_1"},
        },
    )


@pytest.fixture
async def store() -> ObservabilityStore:
    obs = ObservabilityStore.from_dsn(DSN)
    async with obs._engine.begin() as connection:  # noqa: SLF001 - test fixture
        await connection.run_sync(OBS_METADATA.drop_all)
        await connection.run_sync(OBS_METADATA.create_all)
    yield obs
    await obs.close()


async def _count(store: ObservabilityStore, table_name: str) -> int:
    table = store.tables[table_name]
    async with store._session_factory() as session:  # noqa: SLF001
        result = await session.execute(select(func.count()).select_from(table))
        return int(result.scalar_one())


async def test_upsert_idempotent_on_replay(store: ObservabilityStore) -> None:
    batch = [
        make_trace_finished(1, "trace_aaa"),
        make_span_finished(2, "trace_aaa", "span_aaa"),
        make_event(3, "trace_aaa"),
    ]
    first = await store.bulk_upsert(batch)
    assert first >= 3
    second = await store.bulk_upsert(batch)  # replay the same segment
    assert second >= 3
    assert await _count(store, TRACES_TABLE) == 1
    assert await _count(store, SPANS_TABLE) == 1
    assert await _count(store, EVENTS_TABLE) == 1
    assert await _count(store, OPS_USER_EVENTS_TABLE) == 1
    assert await _count(store, OPS_FIRST_USE_TABLE) == 1


async def test_operations_projection_is_idempotent_and_keeps_agent_version(store: ObservabilityStore) -> None:
    batch = [
        make_trace_finished(1, "trace_aaa"),
        make_span_finished(
            2,
            "trace_aaa",
            "span_aina",
            kind="aina",
            target_id="assistant-a",
            target_version="1.2.0",
        ),
    ]
    await store.bulk_upsert(batch)
    await store.bulk_upsert(batch)

    assert await _count(store, OPS_USER_EVENTS_TABLE) == 1
    assert await _count(store, OPS_REQUEST_AGENTS_TABLE) == 1
    assert await _count(store, OPS_FIRST_USE_TABLE) == 2
    rows = await store.list_operation_agent_events(
        tenant_id="tenant_1",
        started_after=datetime(2026, 8, 1, tzinfo=timezone.utc),
        started_before=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    assert rows[0]["agent_id"] == "assistant-a"
    assert rows[0]["agent_version"] == "1.2.0"


async def test_events_keep_microsecond_order(store: ObservabilityStore) -> None:
    later = make_event(
        1,
        "trace_aaa",
        name="model.requested",
        occurred_at="2026-08-06T10:00:02.900000Z",
    )
    earlier = make_event(
        99,
        "trace_aaa",
        name="user.request",
        occurred_at="2026-08-06T10:00:02.100000Z",
    )
    await store.bulk_upsert([later, earlier])

    events = await store.list_events("trace_aaa")

    assert [event["name"] for event in events] == ["user.request", "model.requested"]
    assert events[0]["record_version"] < events[1]["record_version"]


async def test_events_use_wal_order_for_same_microsecond(store: ObservabilityStore) -> None:
    occurred_at = "2026-08-06T10:00:02.123456Z"
    completed = make_event(5, "trace_aaa", name="model.completed", occurred_at=occurred_at)
    requested = make_event(6, "trace_aaa", name="builtin.requested", occurred_at=occurred_at)
    await store.bulk_upsert([requested, completed])

    events = await store.list_events("trace_aaa")

    assert [event["name"] for event in events] == ["model.completed", "builtin.requested"]


async def test_upsert_absolute_values_not_doubled(store: ObservabilityStore) -> None:
    batch = [make_trace_finished(1, "trace_aaa", input_tokens=100)]
    await store.bulk_upsert(batch)
    await store.bulk_upsert(batch)  # replay must not accumulate
    trace = await store.get_trace("trace_aaa")
    assert trace is not None
    assert trace["input_tokens"] == 100
    assert trace["output_tokens"] == 50
    assert trace["compression_count"] == 1
    assert trace["error_count"] == 0
    assert trace["status"] == "completed"


async def test_span_fields_roundtrip(store: ObservabilityStore) -> None:
    await store.bulk_upsert([make_trace_finished(1, "trace_aaa"), make_span_finished(2, "trace_aaa", "span_aaa")])
    spans = await store.list_spans("trace_aaa")
    assert len(spans) == 1
    span = spans[0]
    assert span["span_id"] == "span_aaa"
    assert span["kind"] == "model"
    assert span["model"] == "gpt-test"
    assert span["ttft_ms"] == 150.0
    assert span["raw_io_path"] == "tenant_1/user_1/trace_x/span_y.json.gz"
    assert span["raw_io_sha256"] == "a" * 64
    assert span["raw_io_status"] == "ready"


async def test_bulk_upsert_latest_status_wins(store: ObservabilityStore) -> None:
    running = make_trace_finished(1, "trace_aaa")
    running.payload["status"] = "running"
    running.payload["completed_at"] = None
    finished = make_trace_finished(2, "trace_aaa")
    await store.bulk_upsert([running])
    await store.bulk_upsert([finished])
    trace = await store.get_trace("trace_aaa")
    assert trace is not None
    assert trace["status"] == "completed"
    assert trace["completed_at"] is not None


async def test_aggregate_tokens(store: ObservabilityStore) -> None:
    await store.bulk_upsert(
        [
            make_trace_finished(1, "trace_aaa", input_tokens=100),
            make_trace_finished(2, "trace_bbb", input_tokens=250),
            make_trace_finished(3, "trace_ccc", user_id="user_2", input_tokens=999),
            make_span_finished(4, "trace_aaa", "span_aaa"),
            make_span_finished(5, "trace_bbb", "span_bbb"),
        ]
    )
    start = datetime(2026, 8, 1, tzinfo=timezone.utc)
    end = datetime(2026, 9, 1, tzinfo=timezone.utc)
    summary = await store.aggregate_tokens(tenant_id="tenant_1", user_id="user_1", started_after=start, started_before=end)
    assert summary["trace_count"] == 2
    assert summary["input_tokens"] == 350
    assert summary["output_tokens"] == 100
    model_rows = await store.model_breakdown(tenant_id="tenant_1", user_id="user_1", started_after=start, started_before=end)
    assert model_rows and model_rows[0]["model"] == "gpt-test"
    assert model_rows[0]["call_count"] == 2


async def test_list_traces_permissions_scoping(store: ObservabilityStore) -> None:
    await store.bulk_upsert(
        [
            make_trace_finished(1, "trace_aaa", user_id="user_1"),
            make_trace_finished(2, "trace_bbb", user_id="user_2"),
        ]
    )
    rows = await store.list_traces(tenant_id="tenant_1", user_id="user_1")
    assert [row["trace_id"] for row in rows] == ["trace_aaa"]
    session_rows = await store.list_traces(tenant_id="tenant_1", user_id="user_1", session_id="conv_1")
    assert len(session_rows) == 1


async def test_started_record_cannot_downgrade_finished(store: ObservabilityStore) -> None:
    """Late-arriving started records must not downgrade finished rows
    (review P1-3): started uses INSERT IGNORE, finished fully overwrites."""
    from tianzhou_agent_platform.store.observability_wal import ObsRecord

    finished = make_trace_finished(1, "trace_aaa")
    await store.bulk_upsert([finished])
    started = ObsRecord(
        record_type="trace_started",
        producer_instance_id="node-1-abc",
        sequence_no=99,
        trace_id="trace_aaa",
        payload={
            "legacy_trace_id": "trace_aaa",
            "session_id": "conv_1",
            "user_id": "user_1",
            "tenant_id": "tenant_1",
            "status": "running",
            "started_at": "2026-08-06T10:00:00Z",
        },
    )
    await store.bulk_upsert([started])
    trace = await store.get_trace("trace_aaa")
    assert trace is not None
    assert trace["status"] == "completed"  # not downgraded to running
    assert trace["completed_at"] is not None
    assert trace["input_tokens"] == 100  # not zeroed

    # finished records still fully overwrite (idempotent replay semantics)
    await store.bulk_upsert([finished])
    trace = await store.get_trace("trace_aaa")
    assert trace["status"] == "completed"


async def test_span_started_cannot_downgrade_finished(store: ObservabilityStore) -> None:
    from tianzhou_agent_platform.store.observability_wal import ObsRecord

    await store.bulk_upsert([make_span_finished(2, "trace_aaa", "span_aaa")])
    started = ObsRecord(
        record_type="span_started",
        producer_instance_id="node-1-abc",
        sequence_no=100,
        trace_id="trace_aaa",
        span_id="span_aaa",
        payload={
            "legacy_span_id": "span_aaa",
            "sequence_no": 100,
            "session_id": "conv_1",
            "user_id": "user_1",
            "tenant_id": "tenant_1",
            "kind": "model",
            "name": "chat.completions",
            "status": "running",
            "started_at": "2026-08-06T10:00:01Z",
        },
    )
    await store.bulk_upsert([started])
    span = await store.get_span("span_aaa")
    assert span is not None
    assert span["status"] == "completed"
    assert span["input_tokens"] == 100


async def test_stale_producer_running_rows_are_terminalized(store: ObservabilityStore) -> None:
    interrupted_at = datetime(2026, 8, 6, 10, 0, 10, tzinfo=timezone.utc)
    trace_started = ObsRecord(
        record_type="trace_started",
        producer_instance_id="dead-producer",
        sequence_no=1,
        trace_id="trace_interrupted",
        payload={
            "legacy_trace_id": "trace_interrupted",
            "root_span_id": "span_root",
            "session_id": "conv_1",
            "user_id": "user_1",
            "tenant_id": "tenant_1",
            "status": "running",
            "started_at": "2026-08-06T10:00:00Z",
        },
    )
    span_started = ObsRecord(
        record_type="span_started",
        producer_instance_id="dead-producer",
        sequence_no=2,
        trace_id="trace_interrupted",
        span_id="span_interrupted",
        payload={
            "legacy_span_id": "span_interrupted",
            "parent_span_id": "span_root",
            "sequence_no": 2,
            "session_id": "conv_1",
            "user_id": "user_1",
            "tenant_id": "tenant_1",
            "kind": "model",
            "name": "chat.completions",
            "status": "running",
            "started_at": "2026-08-06T10:00:01Z",
        },
    )
    await store.bulk_upsert([trace_started, span_started])

    counts = await store.fail_interrupted_producers(
        ["dead-producer"], interrupted_at=interrupted_at
    )

    trace = await store.get_trace("trace_interrupted")
    span = await store.get_span("span_interrupted")
    assert counts == {"traces": 1, "spans": 1}
    assert trace is not None
    assert trace["status"] == "failed"
    assert trace["completed_at"] == interrupted_at.replace(tzinfo=None)
    assert trace["attributes"]["unibot.interruption.reason"] == "process_restart"
    assert span is not None
    assert span["status"] == "failed"
    assert span["completed_at"] == interrupted_at.replace(tzinfo=None)
    assert span["attributes"]["unibot.interruption.reason"] == "process_restart"


async def test_out_of_order_finished_does_not_overwrite_newer_state(store: ObservabilityStore) -> None:
    """finished->finished out-of-order replay must not regress a newer
    terminal state (review round 2, P1-2): only newer records apply."""
    from datetime import timedelta

    base = make_trace_finished(1, "trace_aaa")
    # newer: completed, written at T+2
    newer = base.model_copy(deep=True)
    newer.occurred_at = newer.occurred_at + timedelta(seconds=2)
    await store.bulk_upsert([newer])
    # older: approval_required, written at T+1, replayed late
    older = base.model_copy(deep=True)
    older.occurred_at = older.occurred_at + timedelta(seconds=1)
    older.payload = {**older.payload, "status": "approval_required"}
    await store.bulk_upsert([older])
    trace = await store.get_trace("trace_aaa")
    assert trace is not None
    assert trace["status"] == "completed"  # not regressed to approval_required
    assert trace["completed_at"] is not None


async def test_retention_cleanup_deletes_terminal_traces_only(store: ObservabilityStore) -> None:
    """Retention cleanup removes terminal traces (with their spans/events) by
    trace id; approval_required traces survive (review round 3, P1)."""
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    old_finished = make_trace_finished(1, "trace_aaa")
    old_finished.payload = {
        **old_finished.payload,
        "started_at": (now - timedelta(days=400)).isoformat(),
        "completed_at": (now - timedelta(days=399)).isoformat(),
    }
    approval = make_trace_finished(2, "trace_bbb")
    approval.payload = {
        **approval.payload,
        "started_at": (now - timedelta(days=400)).isoformat(),
        "status": "approval_required",
        "completed_at": None,
    }
    await store.bulk_upsert([old_finished, approval, make_span_finished(3, "trace_aaa", "span_aaa")])
    cutoff = now - timedelta(days=365)
    deleted = await store.delete_older_than(cutoff)
    assert deleted[TRACES_TABLE] >= 1
    assert await store.get_trace("trace_aaa") is None  # terminal + old -> removed
    assert await store.get_span("span_aaa") is None  # no orphaned span
    remaining = await store.get_trace("trace_bbb")
    assert remaining is not None and remaining["status"] == "approval_required"


async def test_concurrent_migration_is_safe() -> None:
    """Two Backends upgrading together must both start: the advisory lock
    serializes ALTER and a duplicate-column error is treated as success
    (review round 4, P1)."""
    import asyncio

    from tianzhou_agent_platform.store.observability_store import SPANS_TABLE, TRACES_TABLE

    first = ObservabilityStore.from_dsn(DSN)
    second = ObservabilityStore.from_dsn(DSN)
    async with first._engine.begin() as connection:
        await connection.run_sync(OBS_METADATA.drop_all)
        await connection.run_sync(OBS_METADATA.create_all)
        for table_name in (TRACES_TABLE, SPANS_TABLE, EVENTS_TABLE):
            await connection.execute(text(f"ALTER TABLE {table_name} DROP COLUMN record_version"))
        await connection.execute(text(f"ALTER TABLE {EVENTS_TABLE} DROP COLUMN sequence_no"))
    try:
        await asyncio.gather(first.create_tables(), second.create_tables())
        async with first._engine.begin() as connection:
            result = await connection.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = 'record_version'"
                ),
                {"t": TRACES_TABLE},
            )
            assert int(result.scalar_one()) == 1
    finally:
        await first.close()
        await second.close()


async def test_concurrent_cold_start_create_tables() -> None:
    """Two Backends cold-starting together on an empty database must both
    start: create_all races are tolerated via errno 1050 (review round 4)."""
    import asyncio

    from tianzhou_agent_platform.store.observability_store import TRACES_TABLE

    first = ObservabilityStore.from_dsn(DSN)
    second = ObservabilityStore.from_dsn(DSN)
    async with first._engine.begin() as connection:
        await connection.run_sync(OBS_METADATA.drop_all)
    try:
        await asyncio.gather(first.create_tables(), second.create_tables())
        async with first._engine.begin() as connection:
            result = await connection.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = 'record_version'"
                ),
                {"t": TRACES_TABLE},
            )
            assert int(result.scalar_one()) == 1
    finally:
        await first.close()
        await second.close()


async def test_create_tables_is_idempotent(store: ObservabilityStore) -> None:
    await store.create_tables()  # second run must not raise
    async with store._session_factory() as session:  # noqa: SLF001
        result = await session.execute(text("SELECT 1"))
        assert result.scalar_one() == 1


async def test_record_version_migration_from_old_schema() -> None:
    """Databases created before record_version existed must be upgraded by
    create_tables() (review round 3, P0): old schema -> migration -> write."""
    from sqlalchemy import text as sa_text

    from tianzhou_agent_platform.store.observability_store import SPANS_TABLE, TRACES_TABLE

    obs = ObservabilityStore.from_dsn(DSN)
    async with obs._engine.begin() as connection:
        await connection.run_sync(OBS_METADATA.drop_all)
        # build the OLD schema: current tables minus record_version
        await connection.run_sync(OBS_METADATA.create_all)
        for table_name in (TRACES_TABLE, SPANS_TABLE, EVENTS_TABLE):
            await connection.execute(
                sa_text(f"ALTER TABLE {table_name} DROP COLUMN record_version")
            )
        await connection.execute(sa_text(f"ALTER TABLE {EVENTS_TABLE} DROP COLUMN sequence_no"))
    try:
        await obs.create_tables()  # migration path
        async with obs._engine.begin() as connection:
            for table_name in (TRACES_TABLE, SPANS_TABLE, EVENTS_TABLE):
                result = await connection.execute(
                    sa_text(
                        "SELECT COUNT(*) FROM information_schema.columns "
                        "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = 'record_version'"
                    ),
                    {"t": table_name},
                )
                assert int(result.scalar_one()) == 1, f"{table_name} missing record_version after migration"
            result = await connection.execute(
                sa_text(
                    "SELECT COUNT(*) FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND table_name = :t AND column_name = 'sequence_no'"
                ),
                {"t": EVENTS_TABLE},
            )
            assert int(result.scalar_one()) == 1, f"{EVENTS_TABLE} missing sequence_no after migration"
        # writes work with the migrated schema
        await obs.bulk_upsert([make_trace_finished(1, "trace_aaa"), make_event(2, "trace_aaa")])
        trace = await obs.get_trace("trace_aaa")
        assert trace is not None and trace["status"] == "completed"
        events = await obs.list_events("trace_aaa")
        assert events and events[0]["record_version"] > 0
    finally:
        await obs.close()
