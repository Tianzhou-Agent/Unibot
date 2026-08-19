"""Dual-write pipeline tests: ObservabilityAspect -> OTel span -> WAL ->
ObsIngestWorker -> (fake) ObservabilityStore, plus barrier/fsync semantics,
raw IO persistence and ingest segment recycling.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from tianzhou_agent_platform.core.observability import ObservabilityAspect
from tianzhou_agent_platform.core.observability_writer import ObsIngestWorker
from tianzhou_agent_platform.core.telemetry import DurableWalSpanProcessor, setup_tracer_provider
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.core.chat import ApprovalRecord, LLMCallRecord
from tianzhou_agent_platform.store.observability_raw import RawIoWriter
from tianzhou_agent_platform.store.observability_wal import (
    ObsRecord,
    WalWriter,
    iter_segment_infos,
)


class FakeObsStore:
    """In-memory ObservabilityStore stand-in capturing UPSERT batches."""

    def __init__(self) -> None:
        self.batches: list[list] = []
        self.traces: dict[str, dict] = {}
        self.spans: dict[str, dict] = {}
        self.events: list[dict] = []

    async def get_trace(self, trace_id: str) -> dict | None:
        return self.traces.get(trace_id)

    async def create_tables(self) -> None:
        return None

    async def bulk_upsert(self, records: list) -> int:
        from tianzhou_agent_platform.store.observability_store import _event_values, _span_values, _trace_values

        self.batches.append(records)
        try:
            for record in records:
                if record.record_type in ("trace_started", "trace_finished"):
                    self.traces[record.trace_id] = _trace_values(record)
                elif record.record_type in ("span_started", "span_finished"):
                    self.spans[record.span_id or record.payload["legacy_span_id"]] = _span_values(record)
                elif record.record_type == "event":
                    self.events.append(_event_values(record))
        except Exception:
            import traceback

            traceback.print_exc()
            raise
        return len(records)

    async def close(self) -> None:
        return None


@pytest.fixture
async def pipeline(tmp_path: Path):
    wal_root = tmp_path / "wal"
    raw_root = tmp_path / "raw"
    wal = WalWriter(wal_root, "node-1-abc")
    store = FakeObsStore()
    worker = ObsIngestWorker(wal_root, store, "node-1-abc")
    wal.on_records_flushed = worker.on_records_flushed
    provider = setup_tracer_provider(DurableWalSpanProcessor(wal), service_instance_id="node-1-abc")
    aspect = ObservabilityAspect(
        InMemoryRepository(),
        wal_writer=wal,
        tracer=provider.get_tracer("test"),
        raw_io_writer=RawIoWriter(raw_root),
    )
    wal.start()
    try:
        yield {
            "wal": wal,
            "store": store,
            "worker": worker,
            "aspect": aspect,
            "wal_root": wal_root,
            "raw_root": raw_root,
        }
    finally:
        wal.close()
        await wal.wait_closed()
        await worker.stop()


async def _fresh_resume_aspect(
    pipeline: dict[str, Any], producer_instance_id: str
) -> tuple[Any, WalWriter, ObservabilityAspect]:
    from tianzhou_agent_platform.core.conversation import ConversationCreate

    fresh_repo = InMemoryRepository()
    conversation = await fresh_repo.create_conversation(
        ConversationCreate(user_id="user_1", tenant_id="tenant_1", title="t")
    )
    wal = WalWriter(pipeline["wal_root"], producer_instance_id)
    provider = setup_tracer_provider(
        DurableWalSpanProcessor(wal), service_instance_id=producer_instance_id
    )
    aspect = ObservabilityAspect(
        fresh_repo,
        wal_writer=wal,
        tracer=provider.get_tracer("test"),
        obs_store=pipeline["store"],
    )
    wal.start()
    return conversation, wal, aspect


@pytest.mark.asyncio
async def test_dual_write_produces_wal_records_and_barrier(pipeline) -> None:
    aspect = pipeline["aspect"]
    wal = pipeline["wal"]
    assert await aspect.create_agent_trace(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={"message": "hello"},
        attributes={},
    )
    await aspect.start_span(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        span_id="span_cccccccccccccccccccc",
        parent_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        kind="model",
        name="chat.completions",
        target_id="model-prod",
        input_data={"messages": [{"role": "user", "content": "hello"}]},
    )
    await aspect.record_llm_call(
        LLMCallRecord(
            call_id="llm_1",
            trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            span_id="span_cccccccccccccccccccc",
            endpoint="http://llm/chat/completions",
            model="gpt-test",
            request={"model": "gpt-test", "messages": [{"role": "user", "content": "hello"}]},
            response={"usage": {"prompt_tokens": 10, "completion_tokens": 5}, "content": "hi"},
            status="completed",
        )
    )
    await aspect.finish_span(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "span_cccccccccccccccccccc",
        "completed",
        output_data={"content": "hi"},
        attributes={"input_tokens": 10, "output_tokens": 5, "ttft_ms": 120.0},
        first_output_at=datetime.now(timezone.utc),
    )
    await aspect.record_event(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        kind="approval.requested",
        status="pending",
        details={"approval_id": "appr_1"},
    )
    await aspect.finish_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "completed")

    # barrier: everything is fsynced after finish_trace
    assert wal.flushed_through == wal.sequence_no

    # ingest deterministically via sealed-segment replay
    wal.close()
    await wal.wait_closed()
    await pipeline["worker"]._scan_and_replay()  # noqa: SLF001 - test helper
    store = pipeline["store"]
    assert store.traces["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]["status"] == "completed"
    assert store.traces["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]["session_id"] == "conv_1"
    assert store.traces["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]["message_count"] >= 0

    # P0-2: span records share the trace id with trace records
    trace_ids = set(store.traces.keys())
    assert len(trace_ids) == 1
    (trace_id,) = trace_ids
    assert all(span["trace_id"] == trace_id for span in store.spans.values())
    span = next(s for s in store.spans.values() if s["legacy_span_id"] == "span_cccccccccccccccccccc")
    assert span["kind"] == "model"
    assert span["model"] == "gpt-test"
    assert span["input_tokens"] == 10
    assert span["output_tokens"] == 5
    assert span["ttft_ms"] == 120.0
    assert span["raw_io_status"] == "ready"
    assert span["raw_io_path"] is not None
    assert any(event["name"] == "approval.requested" for event in store.events)

    # raw IO file exists and is redacted
    raw_file = pipeline["raw_root"] / span["raw_io_path"]
    assert raw_file.exists()


@pytest.mark.asyncio
async def test_trace_barrier_uses_its_terminal_record_not_global_sequence(pipeline) -> None:
    aspect = pipeline["aspect"]
    wal = pipeline["wal"]
    await aspect.create_agent_trace(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={},
        attributes={},
    )

    original_submit = wal.submit

    def submit_with_unrelated_record(record: ObsRecord) -> int:
        sequence = original_submit(record)
        if record.record_type == "trace_finished":
            original_submit(
                ObsRecord(
                    record_type="event",
                    producer_instance_id=wal.producer_instance_id,
                    sequence_no=0,
                    trace_id="unrelated_trace",
                    payload={"name": "unrelated"},
                )
            )
        return sequence

    barriers: list[int] = []

    async def capture_barrier(sequence_no: int, *, attempts: int = 3) -> None:
        del attempts
        barriers.append(sequence_no)
        await wal.flush_through(sequence_no)

    wal.submit = submit_with_unrelated_record  # type: ignore[method-assign]
    aspect._flush_with_retry = capture_barrier  # type: ignore[method-assign]
    await aspect.finish_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "completed")

    assert barriers == [wal.sequence_no - 1]


@pytest.mark.asyncio
async def test_ingest_worker_replays_sealed_segments(pipeline) -> None:
    wal = pipeline["wal"]
    store = pipeline["store"]
    assert await pipeline["aspect"].create_agent_trace(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={},
        attributes={},
    )
    await pipeline["aspect"].finish_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "completed")
    wal.close()
    await wal.wait_closed()

    # live callback may have ingested; reset to simulate a replay-from-disk
    store.traces.clear()
    store.spans.clear()
    store.events.clear()
    store.batches.clear()

    await pipeline["worker"]._scan_and_replay()  # noqa: SLF001
    print(
        "DBG2 keys:", list(store.traces.keys()),
        "batches:", [[(r.record_type, r.trace_id) for r in b] for b in store.batches], flush=True,
    )
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in store.traces
    # segments are deleted after successful ingest
    infos = iter_segment_infos(wal._directory)  # type: ignore[attr-defined]
    assert all(info.state != "sealed" for info in infos)


@pytest.mark.asyncio
async def test_ingest_worker_recovers_claiming_segments(pipeline) -> None:
    wal = pipeline["wal"]
    store = pipeline["store"]
    assert await pipeline["aspect"].create_agent_trace(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={},
        attributes={},
    )
    await pipeline["aspect"].finish_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "completed")
    wal.close()
    await wal.wait_closed()

    # simulate a crash mid-claim: rename every sealed segment to .ingesting
    producer_dir = wal._directory  # type: ignore[attr-defined]
    for info in iter_segment_infos(producer_dir):
        if info.state == "sealed":
            info.path.rename(info.path.with_suffix(".ingesting"))

    await pipeline["worker"]._recover_claiming_segments()  # noqa: SLF001
    await pipeline["worker"]._scan_and_replay()  # noqa: SLF001
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in store.traces


@pytest.mark.asyncio
async def test_ingest_worker_claims_orphan_active_segment(pipeline) -> None:
    """A crashed producer's stale .active segment is sealed and replayed
    (review P1-1)."""
    from tianzhou_agent_platform.store.observability_wal import ObsRecord, encode_frame

    wal_root = pipeline["wal_root"]
    other_dir = wal_root / "other-node-123-abc"
    other_dir.mkdir(parents=True)
    # hand-write an .active file with one valid frame (no second WalWriter
    # thread; Windows + multiple to_thread writers is flaky)
    orphan_record = ObsRecord(
        record_type="trace_finished",
        producer_instance_id="other-node-123-abc",
        sequence_no=1,
        trace_id="trace_aaa",
        payload={
            "legacy_trace_id": "trace_aaa",
            "session_id": "conv_1",
            "user_id": "user_1",
            "tenant_id": "tenant_1",
            "status": "completed",
            "started_at": "2026-08-06T10:00:00Z",
        },
    )
    (other_dir / "000000000001.active").write_bytes(encode_frame(orphan_record))

    store = pipeline["store"]
    worker = pipeline["worker"]
    worker.orphan_active_min_age_seconds = 0  # claim immediately
    await worker._scan_and_replay()  # noqa: SLF001
    assert store.traces["trace_aaa"]["status"] == "completed"
    # the orphaned file is consumed (sealed then deleted)
    assert not any(path.suffix in (".active", ".sealed") for path in other_dir.iterdir())


@pytest.mark.asyncio
async def test_ingest_worker_skips_live_producer_with_fresh_heartbeat(pipeline) -> None:
    """A live producer keeps its heartbeat fresh even while idle, so its
    .active segment must never be claimed (review round 2, P1-1)."""
    from tianzhou_agent_platform.store.observability_wal import HEARTBEAT_NAME, encode_frame
    from tianzhou_agent_platform.store.observability_wal import ObsRecord

    wal_root = pipeline["wal_root"]
    other_dir = wal_root / "other-node-live-abc"
    other_dir.mkdir(parents=True)
    (other_dir / "000000000001.active").write_bytes(
        encode_frame(
            ObsRecord(
                record_type="trace_finished",
                producer_instance_id="other-node-live-abc",
                sequence_no=1,
                trace_id="trace_aaa",
                payload={"status": "completed"},
            )
        )
    )
    # fresh heartbeat -> producer considered alive -> not claimed
    heartbeat = other_dir / HEARTBEAT_NAME
    heartbeat.write_text("alive", encoding="utf-8")

    worker = pipeline["worker"]
    # keep the default min age: fresh heartbeat + fresh active -> live producer
    await worker._scan_and_replay()  # noqa: SLF001
    assert not pipeline["store"].traces  # nothing replayed
    assert (other_dir / "000000000001.active").exists()  # segment untouched

    # stale heartbeat + stale active -> treated as crashed -> claimed/replayed
    old = time.time() - 3600
    os.utime(heartbeat, (old, old))
    os.utime(other_dir / "000000000001.active", (old, old))
    worker.orphan_active_min_age_seconds = 0
    await worker._scan_and_replay()  # noqa: SLF001
    assert pipeline["store"].traces["trace_aaa"]["status"] == "completed"


@pytest.mark.asyncio
async def test_ingest_worker_reconciles_only_stale_producers(tmp_path: Path) -> None:
    from tianzhou_agent_platform.store.observability_wal import HEARTBEAT_NAME

    class ReconcileStore(FakeObsStore):
        def __init__(self) -> None:
            super().__init__()
            self.reconciled: list[list[str]] = []

        async def fail_interrupted_producers(
            self,
            producer_instance_ids: list[str],
            *,
            interrupted_at: datetime,
        ) -> dict[str, int]:
            assert interrupted_at.tzinfo is not None
            self.reconciled.append(producer_instance_ids)
            return {"traces": 1, "spans": 2}

    wal_root = tmp_path / "wal"
    stale_dir = wal_root / "stale-producer"
    live_dir = wal_root / "live-producer"
    current_dir = wal_root / "current-producer"
    for directory in (stale_dir, live_dir, current_dir):
        directory.mkdir(parents=True)
        (directory / HEARTBEAT_NAME).write_text("alive", encoding="utf-8")
    old = time.time() - 3600
    os.utime(stale_dir / HEARTBEAT_NAME, (old, old))

    store = ReconcileStore()
    worker = ObsIngestWorker(
        wal_root,
        store,
        "current-producer",
        orphan_active_min_age_seconds=60,
    )
    await worker._reconcile_interrupted_producers()  # noqa: SLF001

    assert store.reconciled == [["stale-producer"]]


@pytest.mark.asyncio
async def test_slow_live_ingest_does_not_block_later_wal_barriers(tmp_path: Path) -> None:
    class BlockingStore(FakeObsStore):
        def __init__(self) -> None:
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def bulk_upsert(self, records: list) -> int:
            self.entered.set()
            await self.release.wait()
            return await super().bulk_upsert(records)

    wal_root = tmp_path / "wal"
    store = BlockingStore()
    worker = ObsIngestWorker(wal_root, store, "node-1-abc", scan_interval_seconds=1)
    wal = WalWriter(wal_root, "node-1-abc", on_records_flushed=worker.on_records_flushed)
    worker.start()
    wal.start()
    try:
        first = wal.submit(
            ObsRecord(
                record_type="event",
                producer_instance_id="node-1-abc",
                sequence_no=0,
                trace_id="trace_aaa",
                payload={"name": "first"},
            )
        )
        await wal.flush_through(first)
        await asyncio.wait_for(store.entered.wait(), timeout=0.2)

        second = wal.submit(
            ObsRecord(
                record_type="event",
                producer_instance_id="node-1-abc",
                sequence_no=0,
                trace_id="trace_aaa",
                payload={"name": "second"},
            )
        )
        await asyncio.wait_for(wal.flush_through(second), timeout=0.2)
    finally:
        store.release.set()
        wal.close()
        await wal.wait_closed()
        await worker.stop()


@pytest.mark.asyncio
async def test_failed_live_ingest_retries_without_new_wal_traffic(tmp_path: Path) -> None:
    class FlakyStore(FakeObsStore):
        def __init__(self) -> None:
            super().__init__()
            self.attempts = 0
            self.succeeded = asyncio.Event()

        async def bulk_upsert(self, records: list) -> int:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("database unavailable")
            result = await super().bulk_upsert(records)
            self.succeeded.set()
            return result

    wal_root = tmp_path / "wal"
    store = FlakyStore()
    worker = ObsIngestWorker(
        wal_root,
        store,
        "node-1-abc",
        scan_interval_seconds=1,
        retry_backoff_seconds=0.01,
    )
    wal = WalWriter(wal_root, "node-1-abc", on_records_flushed=worker.on_records_flushed)
    worker.start()
    wal.start()
    try:
        sequence = wal.submit(
            ObsRecord(
                record_type="event",
                producer_instance_id="node-1-abc",
                sequence_no=0,
                trace_id="trace_aaa",
                payload={"name": "retry"},
            )
        )
        await wal.flush_through(sequence)
        await asyncio.wait_for(store.succeeded.wait(), timeout=0.5)
        assert store.attempts == 2
        assert worker.metrics.ingest_retry_count == 1
    finally:
        wal.close()
        await wal.wait_closed()
        await worker.stop()


@pytest.mark.asyncio
async def test_approval_resume_keeps_same_trace_and_cleans_up(pipeline) -> None:
    """approval_required exports the current root; confirmation builds a
    continuation root under the same trace id, and terminal finish cleans
    per-trace state (review round 2, P1-3 / P2-1)."""
    aspect = pipeline["aspect"]
    wal = pipeline["wal"]
    await aspect.create_agent_trace(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={},
        attributes={},
    )
    aspect.add_trace_token_usage(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        input_tokens=7,
        output_tokens=4,
    )
    # The paused run exports its root so a restart cannot orphan its children.
    await aspect.finish_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "approval_required")
    assert "span_bbbbbbbbbbbbbbbbbbbb" not in aspect._spans  # noqa: SLF001
    assert "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" in aspect._otel_trace_ids  # noqa: SLF001

    # Confirmation rebuilds a continuation root before child spans start.
    root = await aspect.ensure_agent_root_span(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        span_id="span_dddddddddddddddddddd",
        conversation_id="conv_1",
    )
    assert root == "span_dddddddddddddddddddd"
    await aspect.start_span(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        span_id="span_cccccccccccccccccccc",
        parent_span_id="span_dddddddddddddddddddd",
        kind="model",
        name="chat.completions",
    )
    await aspect.finish_span(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "span_cccccccccccccccccccc",
        "completed",
        attributes={"input_tokens": 5, "output_tokens": 3},
    )
    aspect.add_trace_token_usage(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        input_tokens=5,
        output_tokens=3,
    )
    await aspect.finish_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "completed")

    # terminal cleanup: per-trace runtime maps are dropped
    assert "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in aspect._otel_trace_ids  # noqa: SLF001
    assert "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in aspect._trace_contexts  # noqa: SLF001
    assert "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" not in aspect._trace_token_totals  # noqa: SLF001
    assert "span_bbbbbbbbbbbbbbbbbbbb" not in aspect._spans  # noqa: SLF001
    assert "span_dddddddddddddddddddd" not in aspect._spans  # noqa: SLF001
    assert "span_cccccccccccccccccccc" not in aspect._spans  # noqa: SLF001

    wal.close()
    await wal.wait_closed()
    await pipeline["worker"]._scan_and_replay()  # noqa: SLF001
    store = pipeline["store"]
    trace = store.traces["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert trace["status"] == "completed"
    assert trace["input_tokens"] == 12
    assert trace["output_tokens"] == 7
    roots = [span for span in store.spans.values() if span["kind"] == "agent"]
    assert {span["legacy_span_id"] for span in roots} == {
        "span_bbbbbbbbbbbbbbbbbbbb",
        "span_dddddddddddddddddddd",
    }
    continuation_root = next(
        span for span in roots if span["legacy_span_id"] == "span_dddddddddddddddddddd"
    )
    assert trace["root_span_id"] == continuation_root["span_id"]
    assert all(
        span["parent_span_id"] is None or span["parent_span_id"] in store.spans
        for span in store.spans.values()
    )
    # P1-3: every span of the resumed trace shares the trace id
    trace_ids = {span["trace_id"] for span in store.spans.values()}
    assert trace_ids == {"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}


@pytest.mark.asyncio
async def test_cross_instance_approval_resume_rebuilds_trace_context(pipeline) -> None:
    """A new ObservabilityAspect (other instance / after restart) recovers the
    trace context from the OBS pipeline and keeps the same trace id for
    resumed spans (review round 3, P1)."""
    from tianzhou_agent_platform.core.observability import ObservabilityAspect as Aspect
    from tianzhou_agent_platform.core.telemetry import DurableWalSpanProcessor as Processor
    from tianzhou_agent_platform.core.telemetry import setup_tracer_provider as setup

    aspect = pipeline["aspect"]
    wal = pipeline["wal"]
    await aspect.create_agent_trace(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={},
        attributes={},
    )
    await aspect.finish_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "approval_required")
    wal.close()
    await wal.wait_closed()
    await pipeline["worker"]._scan_and_replay()  # noqa: SLF001
    assert pipeline["store"].traces  # approval trace row is visible
    paused_trace = pipeline["store"].traces["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert paused_trace["root_span_id"] in pipeline["store"].spans

    # a brand-new instance (fresh maps, same repository + OBS store)
    store = pipeline["store"]
    new_wal = WalWriter(pipeline["wal_root"], "node-2-abc")
    provider = setup(Processor(new_wal), service_instance_id="node-2-abc")
    new_aspect = Aspect(
        aspect._repository,  # same business repository (shared MySQL in prod)
        wal_writer=new_wal,
        tracer=provider.get_tracer("test"),
        obs_store=store,
    )
    new_wal.start()
    try:
        continuation_root_id = await new_aspect.ensure_agent_root_span(
            "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            span_id="span_dddddddddddddddddddd",
            conversation_id="conv_1",
        )
        assert continuation_root_id == "span_dddddddddddddddddddd"
        await new_aspect.start_span(
            "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            span_id="span_cccccccccccccccccccc",
            parent_span_id=continuation_root_id,
            kind="model",
            name="chat.completions",
        )
        otel_trace_id = new_aspect._otel_trace_ids["trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]  # noqa: SLF001
        span = new_aspect._spans["span_cccccccccccccccccccc"]  # noqa: SLF001
        assert span.context.trace_id.to_bytes(16, "big").hex() == otel_trace_id
        assert otel_trace_id == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        # the rebuilt continuation root shares the same trace id
        root = new_aspect._spans[continuation_root_id]  # noqa: SLF001
        assert root.context.trace_id.to_bytes(16, "big").hex() == otel_trace_id
    finally:
        new_wal.close()
        await new_wal.wait_closed()


@pytest.mark.asyncio
async def test_fallback_records_use_real_otel_span_ids(pipeline) -> None:
    """The direct-write fallback must use 16-hex OTel span ids (mapped at span
    creation), not 37-char legacy ids that overflow VARCHAR(32)
    (review round 3, P1)."""
    from tianzhou_agent_platform.core.chat import TraceRecord, TraceSpan
    aspect = pipeline["aspect"]
    aspect._otel_trace_ids["trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"] = "a" * 32  # noqa: SLF001
    aspect._otel_span_ids["span_cccccccccccccccccccc"] = "b" * 16  # noqa: SLF001
    trace = TraceRecord(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        spans=[
            TraceSpan(
                span_id="span_bbbbbbbbbbbbbbbbbbbb",
                kind="agent",
                name="agent.run",
                status="completed",
            ),
            TraceSpan(
                span_id="span_cccccccccccccccccccc",
                parent_span_id="span_bbbbbbbbbbbbbbbbbbbb",
                kind="model",
                name="chat.completions",
                status="completed",
            ),
        ],
        status="completed",
    )
    records = aspect._build_fallback_records("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "completed", trace)  # noqa: SLF001
    span_records = [r for r in records if r.record_type == "span_finished"]
    assert len(span_records) == 2
    for record in span_records:
        assert len(record.span_id or "") == 16, f"fallback span_id must be 16 hex, got {record.span_id!r}"
    model_record = next(r for r in span_records if r.payload["legacy_span_id"] == "span_cccccccccccccccccccc")
    assert model_record.span_id == "b" * 16
    assert model_record.payload["parent_span_id"] in (None, "b" * 16) or len(
        model_record.payload["parent_span_id"] or ""
    ) == 16


@pytest.mark.asyncio
async def test_concurrent_resume_builds_single_continuation_root(pipeline) -> None:
    """Concurrent resumes of the same approval trace must build exactly one
    continuation root span (review round 3 should-fix)."""
    import asyncio as asyncio_mod

    from tianzhou_agent_platform.core.observability import ObservabilityAspect as Aspect
    from tianzhou_agent_platform.core.telemetry import DurableWalSpanProcessor as Processor
    from tianzhou_agent_platform.core.telemetry import setup_tracer_provider as setup

    aspect = pipeline["aspect"]
    wal = pipeline["wal"]
    await aspect.create_agent_trace(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={},
        attributes={},
    )
    await aspect.finish_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "approval_required")
    wal.close()
    await wal.wait_closed()
    await pipeline["worker"]._scan_and_replay()  # noqa: SLF001

    store = pipeline["store"]
    new_wal = WalWriter(pipeline["wal_root"], "node-3-abc")
    provider = setup(Processor(new_wal), service_instance_id="node-3-abc")
    new_aspect = Aspect(
        aspect._repository,  # noqa: SLF001
        wal_writer=new_wal,
        tracer=provider.get_tracer("test"),
        obs_store=store,
    )
    new_wal.start()
    try:
        await asyncio_mod.gather(
            new_aspect.start_span(
                "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                span_id="span_c1",
                parent_span_id="span_bbbbbbbbbbbbbbbbbbbb",
                kind="model",
                name="chat.completions",
            ),
            new_aspect.start_span(
                "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                span_id="span_c2",
                parent_span_id="span_bbbbbbbbbbbbbbbbbbbb",
                kind="model",
                name="chat.completions",
            ),
        )
        roots = [
            span_id
            for span_id, span in new_aspect._spans.items()  # noqa: SLF001
            if span.attributes.get("unibot.span_role") == "root"
        ]
        assert len(roots) == 1, f"expected a single continuation root, got {roots}"
    finally:
        new_wal.close()
        await new_wal.wait_closed()


@pytest.mark.asyncio
async def test_fresh_instance_resume_without_obs_row(pipeline) -> None:
    """A truly fresh instance (empty repository, no OBS row yet) must still
    recover the trace context from the business conversation record and the
    legacy id (review round 4, P1)."""
    from tianzhou_agent_platform.core.conversation import ConversationCreate
    from tianzhou_agent_platform.core.observability import ObservabilityAspect as Aspect
    from tianzhou_agent_platform.core.repository import InMemoryRepository as Repo
    from tianzhou_agent_platform.core.telemetry import DurableWalSpanProcessor as Processor
    from tianzhou_agent_platform.core.telemetry import setup_tracer_provider as setup

    fresh_repo = Repo()
    conversation = await fresh_repo.create_conversation(
        ConversationCreate(user_id="user_1", tenant_id="tenant_1", title="t")
    )
    new_wal = WalWriter(pipeline["wal_root"], "node-4-abc")
    provider = setup(Processor(new_wal), service_instance_id="node-4-abc")
    # obs_store left empty on purpose: the OBS row may not be ingested yet
    new_aspect = Aspect(
        fresh_repo,
        wal_writer=new_wal,
        tracer=provider.get_tracer("test"),
        obs_store=pipeline["store"],  # store has no trace_aaa row
    )
    new_wal.start()
    try:
        # A pre-root event can recover only the trace id and cache anonymous
        # ownership. The later resume call must enrich that cached context.
        await new_aspect._ensure_trace_context("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")  # noqa: SLF001
        anonymous = new_aspect._trace_contexts["trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]  # noqa: SLF001
        assert anonymous.user_id == "anonymous"
        # the real resume flow enters through ensure_agent_root_span, which
        # carries the conversation id for the business-data fallback
        root = await new_aspect.ensure_agent_root_span(
            "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            span_id="span_bbbbbbbbbbbbbbbbbbbb",
            conversation_id=conversation.id,
        )
        assert root == "span_bbbbbbbbbbbbbbbbbbbb"
        await new_aspect.start_span(
            "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            span_id="span_cccccccccccccccccccc",
            parent_span_id="span_bbbbbbbbbbbbbbbbbbbb",
            kind="model",
            name="chat.completions",
        )
        otel_trace_id = new_aspect._otel_trace_ids["trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]  # noqa: SLF001
        assert otel_trace_id == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"  # derived from legacy id
        context = new_aspect._trace_contexts["trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]  # noqa: SLF001
        assert context.user_id == "user_1"  # recovered from the conversation
        assert context.tenant_id == "tenant_1"
        span = new_aspect._spans["span_cccccccccccccccccccc"]  # noqa: SLF001
        assert span.context.trace_id.to_bytes(16, "big").hex() == otel_trace_id
    finally:
        new_wal.close()
        await new_wal.wait_closed()


@pytest.mark.asyncio
async def test_terminal_cleanup_drops_span_id_mappings(pipeline) -> None:
    """Terminal cleanup must drop the whole trace's span-id mappings,
    including spans already finished and removed from _spans
    (review round 4, P2)."""
    aspect = pipeline["aspect"]
    await aspect.create_agent_trace(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={},
        attributes={},
    )
    await aspect.start_span(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        span_id="span_cccccccccccccccccccc",
        parent_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        kind="model",
        name="chat.completions",
    )
    await aspect.finish_span(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "span_cccccccccccccccccccc",
        "completed",
        attributes={"input_tokens": 5, "output_tokens": 3},
    )
    assert "span_cccccccccccccccccccc" in aspect._otel_span_ids  # noqa: SLF001 - kept for fallback
    assert "span_cccccccccccccccccccc" not in aspect._spans  # noqa: SLF001 - finished
    wal = pipeline["wal"]
    await aspect.finish_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "completed")
    # after terminal cleanup the mapping is gone even though the span was
    # already removed from _spans earlier
    assert "span_cccccccccccccccccccc" not in aspect._otel_span_ids  # noqa: SLF001
    assert aspect._span_ids_by_trace == {}  # noqa: SLF001
    wal.close()
    await wal.wait_closed()


@pytest.mark.asyncio
async def test_terminal_trace_cleans_runtime_state_when_wal_and_mysql_fallback_fail(
    pipeline, monkeypatch
) -> None:
    class FailingStore:
        async def bulk_upsert(self, records: list[ObsRecord]) -> int:
            raise RuntimeError("OBS MySQL unavailable")

    aspect = pipeline["aspect"]
    trace_id = "trace_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    root_span_id = "span_ffffffffffffffffffffffffffffffff"
    await aspect.create_agent_trace(
        trace_id=trace_id,
        root_span_id=root_span_id,
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={},
        attributes={},
    )
    aspect.add_trace_token_usage(trace_id, input_tokens=2, output_tokens=1)

    async def fail_barrier(sequence_no: int, *, attempts: int = 3) -> None:
        from tianzhou_agent_platform.store.observability_wal import WalGapError

        raise WalGapError(f"forced gap at {sequence_no}")

    monkeypatch.setattr(aspect, "_flush_with_retry", fail_barrier)
    aspect._obs_store = FailingStore()  # noqa: SLF001

    await aspect.finish_trace(trace_id, "completed")

    assert trace_id not in aspect._otel_trace_ids  # noqa: SLF001
    assert trace_id not in aspect._trace_contexts  # noqa: SLF001
    assert trace_id not in aspect._trace_token_totals  # noqa: SLF001
    assert root_span_id not in aspect._spans  # noqa: SLF001


@pytest.mark.asyncio
async def test_fresh_instance_full_pipeline(pipeline) -> None:
    """Complete cold-start resume flow on a truly fresh instance:
    event -> span start -> span finish -> trace finish -> WAL replay
    (review round 5: approval events must not be lost, spans must finish)."""
    from tianzhou_agent_platform.core.conversation import ConversationCreate
    from tianzhou_agent_platform.core.observability import ObservabilityAspect as Aspect
    from tianzhou_agent_platform.core.repository import InMemoryRepository as Repo
    from tianzhou_agent_platform.core.telemetry import DurableWalSpanProcessor as Processor
    from tianzhou_agent_platform.core.telemetry import setup_tracer_provider as setup

    fresh_repo = Repo()
    conversation = await fresh_repo.create_conversation(
        ConversationCreate(user_id="user_1", tenant_id="tenant_1", title="t")
    )
    new_wal = WalWriter(pipeline["wal_root"], "node-5-abc")
    provider = setup(Processor(new_wal), service_instance_id="node-5-abc")
    new_aspect = Aspect(
        fresh_repo,
        wal_writer=new_wal,
        tracer=provider.get_tracer("test"),
        obs_store=pipeline["store"],  # no OBS row yet (ingest lag)
    )
    new_wal.start()
    try:
        trace_id = "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        # 1) approval confirmation event must survive on a fresh instance
        await new_aspect.record_event(
            trace_id,
            kind="approval.confirmed",
            status="completed",
            conversation_id=conversation.id,
            details={"approval_id": "appr_1"},
        )
        # 2) resume: root span + model span
        root = await new_aspect.ensure_agent_root_span(
            trace_id, span_id="span_bbbbbbbbbbbbbbbbbbbb", conversation_id=conversation.id
        )
        assert root == "span_bbbbbbbbbbbbbbbbbbbb"
        await new_aspect.start_span(
            trace_id,
            span_id="span_cccccccccccccccccccc",
            parent_span_id="span_bbbbbbbbbbbbbbbbbbbb",
            kind="model",
            name="chat.completions",
            input_data={"messages": [{"role": "user", "content": "hi"}]},
        )
        # 3) span must finish (span_finished record with tokens)
        await new_aspect.finish_span(
            trace_id,
            "span_cccccccccccccccccccc",
            "completed",
            output_data={"content": "hi"},
            attributes={"input_tokens": 5, "output_tokens": 3},
        )
        # agent flow also finishes the root span with conversation totals
        await new_aspect.finish_span(
            trace_id,
            "span_bbbbbbbbbbbbbbbbbbbb",
            "completed",
            attributes={"input_tokens": 5, "output_tokens": 3},
        )
        # 4) trace finish
        await new_aspect.finish_trace(trace_id, "completed")
    finally:
        new_wal.close()
        await new_wal.wait_closed()

    # 5) WAL replay -> OBS rows
    await pipeline["worker"]._scan_and_replay()  # noqa: SLF001
    store = pipeline["store"]
    trace = store.traces["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]
    assert trace["status"] == "completed"
    assert trace["session_id"] == conversation.id
    assert trace["user_id"] == "user_1"
    assert trace["tenant_id"] == "tenant_1"
    assert trace["input_tokens"] == 5 and trace["output_tokens"] == 3
    approval_event = next(event for event in store.events if event["name"] == "approval.confirmed")
    assert approval_event["session_id"] == conversation.id
    assert approval_event["user_id"] == "user_1"
    assert approval_event["tenant_id"] == "tenant_1"
    span = next(
        s for s in store.spans.values() if s["legacy_span_id"] == "span_cccccccccccccccccccc"
    )
    assert span["status"] == "completed"
    assert span["input_tokens"] == 5
    assert span["trace_id"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    # exactly one root (span_bbbb) + the model span: the derived-key ghost
    # root from the earlier record_event recovery must not produce a row
    # (review round 5)
    assert {s["legacy_span_id"] for s in store.spans.values()} == {
        "span_bbbbbbbbbbbbbbbbbbbb",
        "span_cccccccccccccccccccc",
    }
    root_span = next(
        s for s in store.spans.values() if s["legacy_span_id"] == "span_bbbbbbbbbbbbbbbbbbbb"
    )
    assert trace["root_span_id"] == root_span["span_id"]


@pytest.mark.asyncio
async def test_fresh_instance_denial_finishes_continuation_root(pipeline) -> None:
    conversation, wal, aspect = await _fresh_resume_aspect(pipeline, "node-6-abc")
    trace_id = "trace_11111111111111111111111111111111"
    try:
        await aspect.record_event(
            trace_id,
            kind="approval.denied",
            status="completed",
            conversation_id=conversation.id,
            details={"approval_id": "appr_denied"},
        )
        await aspect.finish_trace(trace_id, "completed")
        assert wal.flushed_through == wal.sequence_no
    finally:
        wal.close()
        await wal.wait_closed()

    await pipeline["worker"]._scan_and_replay()  # noqa: SLF001
    store = pipeline["store"]
    trace = store.traces["11111111111111111111111111111111"]
    assert trace["session_id"] == conversation.id
    assert trace["user_id"] == "user_1"
    assert trace["tenant_id"] == "tenant_1"
    assert trace["root_span_id"] is not None
    root = store.spans[trace["root_span_id"]]
    assert root["kind"] == "agent"
    assert root["status"] == "completed"
    assert root["session_id"] == conversation.id
    assert len([span for span in store.spans.values() if span["trace_id"] == trace["trace_id"]]) == 1


@pytest.mark.asyncio
async def test_fresh_instance_cancellation_finishes_continuation_root(pipeline) -> None:
    conversation, wal, aspect = await _fresh_resume_aspect(pipeline, "node-7-abc")
    trace_id = "trace_22222222222222222222222222222222"
    approval = ApprovalRecord(
        id="appr_cancelled",
        conversation_id=conversation.id,
        user_id="user_1",
        tenant_id="tenant_1",
        trace_id=trace_id,
        tool_calls=[],
        capability_names=[],
    )
    try:
        await aspect.record_cancelled_approvals([approval])
        assert wal.flushed_through == wal.sequence_no
    finally:
        wal.close()
        await wal.wait_closed()

    await pipeline["worker"]._scan_and_replay()  # noqa: SLF001
    store = pipeline["store"]
    trace = store.traces["22222222222222222222222222222222"]
    assert trace["session_id"] == conversation.id
    assert trace["user_id"] == "user_1"
    assert trace["tenant_id"] == "tenant_1"
    assert trace["root_span_id"] is not None
    root = store.spans[trace["root_span_id"]]
    assert root["kind"] == "agent"
    assert root["status"] == "completed"
    assert root["session_id"] == conversation.id
    assert len([span for span in store.spans.values() if span["trace_id"] == trace["trace_id"]]) == 1


@pytest.mark.asyncio
async def test_disabled_pipeline_keeps_legacy_only(tmp_path: Path) -> None:
    repository = InMemoryRepository()
    aspect = ObservabilityAspect(repository)
    assert await aspect.create_agent_trace(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={"message": "hi"},
        attributes={},
    )
    trace = await repository.get_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert trace is not None
    assert trace.root_span_id == "span_bbbbbbbbbbbbbbbbbbbb"


@pytest.mark.asyncio
async def test_finish_trace_marks_interrupted_status(pipeline) -> None:
    aspect = pipeline["aspect"]
    await aspect.create_agent_trace(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={},
        attributes={},
    )
    # simulate a run that never finishes a child span; finish_trace completes
    await aspect.finish_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "failed")
    wal = pipeline["wal"]
    wal.close()
    await wal.wait_closed()
    await pipeline["worker"]._scan_and_replay()  # noqa: SLF001
    store = pipeline["store"]
    assert store.traces["aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]["status"] == "failed"


@pytest.mark.asyncio
async def test_raw_io_not_written_when_disabled(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    wal = WalWriter(tmp_path / "wal", "node-1-abc")
    provider = setup_tracer_provider(DurableWalSpanProcessor(wal), service_instance_id="node-1-abc")
    aspect = ObservabilityAspect(InMemoryRepository(), wal_writer=wal, tracer=provider.get_tracer("t"))
    wal.start()
    await aspect.create_agent_trace(
        trace_id="trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={},
        attributes={},
    )
    await aspect.start_span(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        span_id="span_cccccccccccccccccccc",
        parent_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        kind="tool",
        name="tool.run",
        input_data={"args": {"x": 1}},
    )
    await aspect.finish_span(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "span_cccccccccccccccccccc",
        "completed",
        output_data={"result": 2},
    )
    await aspect.finish_trace("trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "completed")
    wal.close()
    await wal.wait_closed()
    assert not raw_root.exists()


@pytest.mark.asyncio
async def test_model_raw_io_preserves_all_provider_attempts(tmp_path: Path) -> None:
    raw_root = tmp_path / "raw"
    wal = WalWriter(tmp_path / "wal", "node-1-abc")
    provider = setup_tracer_provider(DurableWalSpanProcessor(wal), service_instance_id="node-1-abc")
    aspect = ObservabilityAspect(
        InMemoryRepository(),
        wal_writer=wal,
        tracer=provider.get_tracer("attempts"),
        raw_io_writer=RawIoWriter(raw_root),
    )
    wal.start()
    trace_id = "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    span_id = "span_cccccccccccccccccccc"
    await aspect.create_agent_trace(
        trace_id=trace_id,
        root_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        input_data={},
        attributes={},
    )
    await aspect.start_span(
        trace_id,
        span_id=span_id,
        parent_span_id="span_bbbbbbbbbbbbbbbbbbbb",
        kind="model",
        name="model.complete",
    )
    base = LLMCallRecord(
        call_id="attempt_1",
        trace_id=trace_id,
        span_id=span_id,
        endpoint="https://model.invalid/chat",
        model="model-a",
        request={"messages": [{"role": "user", "content": "hello"}]},
    )
    await aspect.record_llm_call(base)
    await aspect.record_llm_call(
        base.model_copy(
            update={
                "status": "failed",
                "response": {"status_code": 400},
                "error": "unsupported option",
            }
        )
    )
    second = base.model_copy(
        update={
            "call_id": "attempt_2",
        }
    )
    await aspect.record_llm_call(second)
    await aspect.record_llm_call(
        second.model_copy(
            update={
                "status": "completed",
                "response": {"content": "ok"},
                "error": None,
            }
        )
    )
    await aspect.finish_span(trace_id, span_id, "completed", output_data={"content": "ok"})
    await aspect.finish_trace(trace_id, "completed")
    wal.close()
    await wal.wait_closed()

    raw_file = next(raw_root.rglob("*.json.gz"))
    with gzip.open(raw_file, "rt", encoding="utf-8") as file:
        document = json.load(file)
    assert [attempt["status"] for attempt in document["attempts"]] == [
        "failed",
        "completed",
    ]
    assert document["response"] == {"content": "ok"}


def test_span_to_record_mapping() -> None:
    """Direct mapping check: business attributes land in the right payload keys."""
    from opentelemetry.sdk.trace import TracerProvider

    class FakeWal:
        producer_instance_id = "node-1-abc"

        def __init__(self) -> None:
            self.records: list = []

        def submit(self, record) -> int:
            self.records.append(record)
            return len(self.records)

    wal = FakeWal()
    provider = TracerProvider()
    provider.add_span_processor(DurableWalSpanProcessor(wal))  # type: ignore[arg-type]
    tracer = provider.get_tracer("test")
    span = tracer.start_span("agent.run")
    span.set_attribute("unibot.span_role", "root")
    span.set_attribute("unibot.trace_id", "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    span.set_attribute("unibot.span_id", "span_bbbbbbbbbbbbbbbbbbbb")
    span.set_attribute("unibot.span_kind", "agent")
    span.set_attribute("session.id", "conv_1")
    span.set_attribute("user.id", "user_1")
    span.set_attribute("unibot.tenant.id", "tenant_1")
    span.set_attribute("unibot.target_id", "assistant-a")
    span.set_attribute("unibot.target_version", "1.2.0")
    span.set_attribute("gen_ai.usage.input_tokens", 42)
    span.end()

    assert len(wal.records) == 1
    record = wal.records[0]
    assert record.record_type == "span_finished"
    assert record.trace_id == span.context.trace_id.to_bytes(16, "big").hex()
    assert record.payload["kind"] == "agent"
    assert record.payload["name"] == "agent.run"
    assert record.payload["session_id"] == "conv_1"
    assert record.payload["user_id"] == "user_1"
    assert record.payload["tenant_id"] == "tenant_1"
    assert record.payload["target_id"] == "assistant-a"
    assert record.payload["target_version"] == "1.2.0"
    assert record.payload["input_tokens"] == 42
    assert record.payload["legacy_span_id"] == "span_bbbbbbbbbbbbbbbbbbbb"


def test_span_suppression_is_captured_when_span_starts() -> None:
    from opentelemetry.sdk.trace import TracerProvider

    from tianzhou_agent_platform.core.observation_context import suppress_observation

    class FakeWal:
        producer_instance_id = "node-1-abc"

        def __init__(self) -> None:
            self.records: list = []

        def submit(self, record) -> int:
            self.records.append(record)
            return len(self.records)

    wal = FakeWal()
    provider = TracerProvider()
    provider.add_span_processor(DurableWalSpanProcessor(wal))  # type: ignore[arg-type]
    tracer = provider.get_tracer("test-suppression")
    with suppress_observation():
        suppressed = tracer.start_span("observability.internal")
    suppressed.end()

    normal = tracer.start_span("business.operation")
    normal.set_attribute("unibot.trace_id", "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    with suppress_observation():
        normal.end()

    assert [record.payload["name"] for record in wal.records] == [
        "business.operation"
    ]
