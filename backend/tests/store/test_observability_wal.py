"""WAL unit tests: frame codec/CRC, half-written tail recovery, middle
corruption isolation, rotation, flush_through barriers and concurrent order.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from tianzhou_agent_platform.store.observability_wal import (
    MAX_PAYLOAD_LENGTH,
    WalError,
    WalFlushTimeoutError,
    WalMetrics,
    WalRecovery,
    WalSegment,
    WalWriter,
    build_producer_instance_id,
    encode_frame,
    iter_segment_infos,
    read_frames,
    validate_segment_file,
)
from tianzhou_agent_platform.store.observability_wal import ObsRecord


def make_record(
    sequence_no: int,
    *,
    trace_id: str = "trace_aaa",
    record_type: str = "span_finished",
    span_id: str = "span_bbb",
) -> ObsRecord:
    return ObsRecord(
        record_type=record_type,  # type: ignore[arg-type]
        producer_instance_id="node-1-abc",
        sequence_no=sequence_no,
        trace_id=trace_id,
        span_id=span_id,
        payload={"status": "completed", "kind": "model", "name": "test"},
    )


@pytest.mark.asyncio
async def test_frame_roundtrip() -> None:
    record = make_record(1)
    frames, corrupt, trailing = read_frames(encode_frame(record))
    assert corrupt is None
    assert not trailing
    assert len(frames) == 1
    assert frames[0].record.sequence_no == 1
    assert frames[0].record.trace_id == "trace_aaa"
    assert frames[0].record.payload["name"] == "test"


@pytest.mark.asyncio
async def test_frame_bad_magic_isolated() -> None:
    frame = bytearray(encode_frame(make_record(1)))
    frame[0] = ord("X")
    frames, corrupt, trailing = read_frames(bytes(frame))
    assert frames == []
    assert corrupt == 0
    assert not trailing


@pytest.mark.asyncio
async def test_crc_failure_in_middle_isolated() -> None:
    first = encode_frame(make_record(1))
    second = bytearray(encode_frame(make_record(2)))
    second[-1] ^= 0xFF  # corrupt CRC of the second frame
    frames, corrupt, trailing = read_frames(first + bytes(second))
    assert len(frames) == 1
    assert corrupt == len(first)
    assert not trailing


@pytest.mark.asyncio
async def test_trailing_half_frame_truncated() -> None:
    first = encode_frame(make_record(1))
    second = encode_frame(make_record(2))
    truncated = first + second[: len(second) - 5]
    frames, corrupt, trailing = read_frames(truncated)
    assert len(frames) == 1
    assert corrupt is None
    assert trailing


@pytest.mark.asyncio
async def test_recovery_truncates_active_tail(tmp_path: Path) -> None:
    writer = WalWriter(tmp_path, "node-1-abc")
    writer.start()
    writer.submit(make_record(1))
    await writer.flush_through(1)
    writer.close()
    await writer.wait_closed()

    active = iter_segment_infos(tmp_path / "node-1-abc")[0]
    assert active.state == "sealed"  # closed writer seals the segment

    # append garbage half-frame to a fresh active file
    active_dir = tmp_path / "node-1-abc"
    corrupted = active_dir / "000000000002.active"
    corrupted.write_bytes(b"UOBS\x01\x00\x00\x00\x05{partial")

    recovery = WalRecovery(tmp_path)
    result = recovery.scan("node-1-abc")
    assert result.truncated_trailing_bytes == len(b"UOBS\x01\x00\x00\x00\x05{partial")
    replayed = validate_segment_file(corrupted)
    assert replayed == ([], None, False) or replayed[0] == []


@pytest.mark.asyncio
async def test_recovery_isolates_middle_corruption(tmp_path: Path) -> None:
    directory = tmp_path / "node-1-abc"
    directory.mkdir(parents=True)
    first = encode_frame(make_record(1))
    bad = bytearray(encode_frame(make_record(2)))
    bad[10] ^= 0xFF  # corrupt payload byte -> CRC failure in the middle
    good = encode_frame(make_record(3))
    (directory / "000000000001.sealed").write_bytes(first + bytes(bad) + good)

    result = WalRecovery(tmp_path).scan("node-1-abc")
    assert len(result.corrupt_segments) == 1
    assert not (directory / "000000000001.sealed").exists()
    assert (directory / "000000000001.corrupt").exists()


@pytest.mark.asyncio
async def test_writer_flush_through_barrier(tmp_path: Path) -> None:
    writer = WalWriter(tmp_path, "node-1-abc")
    writer.start()
    seq = writer.submit(make_record(1))
    await writer.flush_through(seq)
    assert writer.flushed_through >= seq
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_writer_batches_and_fsyncs_in_order(tmp_path: Path) -> None:
    writer = WalWriter(tmp_path, "node-1-abc", fsync_batch_max=3)
    writer.start()
    sequences = [writer.submit(make_record(i, trace_id=f"trace_{i}")) for i in range(1, 20)]
    await writer.flush_through(max(sequences))
    writer.close()
    await writer.wait_closed()

    sealed = writer._directory / iter_segment_infos(writer._directory)[0].path.name  # type: ignore[attr-defined]
    data = sealed.read_bytes()
    frames, corrupt, trailing = read_frames(data)
    assert corrupt is None and not trailing
    assert [frame.record.sequence_no for frame in frames] == list(range(1, 20))
    assert writer.metrics.fsync_count >= 1


@pytest.mark.asyncio
async def test_concurrent_submits_preserve_order(tmp_path: Path) -> None:
    writer = WalWriter(tmp_path, "node-1-abc", queue_capacity=1_000)
    writer.start()

    def submit_batch(worker: int) -> list[int]:
        return [
            writer.submit(make_record(0, trace_id=f"trace_{worker}_{index}"))
            for index in range(25)
        ]

    batches = await asyncio.gather(
        *(asyncio.to_thread(submit_batch, worker) for worker in range(8))
    )
    sequences = [sequence for batch in batches for sequence in batch]
    await writer.flush_through(writer.sequence_no)
    writer.close()
    await writer.wait_closed()

    infos = iter_segment_infos(writer._directory)  # type: ignore[attr-defined]
    all_frames = []
    for info in infos:
        frames, corrupt, trailing = validate_segment_file(info.path)
        assert corrupt is None and not trailing
        all_frames.extend(frame.record.sequence_no for frame in frames)
    assert sorted(sequences) == list(range(1, 201))
    assert all_frames == list(range(1, 201))


@pytest.mark.asyncio
async def test_queue_full_sync_fallback_preserves_order(tmp_path: Path) -> None:
    writer = WalWriter(
        tmp_path,
        "node-1-abc",
        queue_capacity=2,
        fsync_batch_max=1,
        queue_full_wait_seconds=0.01,
    )
    writer.start()
    sequences = [writer.submit(make_record(0, trace_id=f"trace_{i}")) for i in range(30)]
    await writer.flush_through(max(sequences))
    writer.close()
    await writer.wait_closed()

    infos = iter_segment_infos(writer._directory)  # type: ignore[attr-defined]
    all_frames = []
    for info in infos:
        frames, corrupt, trailing = validate_segment_file(info.path)
        assert corrupt is None and not trailing
        all_frames.extend(frame.record.sequence_no for frame in frames)
    assert all_frames == sorted(all_frames)
    assert len(all_frames) == 30


@pytest.mark.asyncio
async def test_rotation_by_size(tmp_path: Path) -> None:
    writer = WalWriter(tmp_path, "node-1-abc", max_segment_bytes=1_024, rotation_interval_seconds=3600)
    writer.start()
    big_payload = {"status": "completed", "kind": "model", "name": "x" * 512}
    for i in range(40):
        record = ObsRecord(
            record_type="span_finished",
            producer_instance_id="node-1-abc",
            sequence_no=0,
            trace_id=f"trace_{i}",
            span_id=f"span_{i}",
            payload=big_payload,
        )
        writer.submit(record)
    await writer.flush_through(writer.sequence_no)
    writer.close()
    await writer.wait_closed()

    infos = iter_segment_infos(writer._directory)  # type: ignore[attr-defined]
    sealed = [info for info in infos if info.state == "sealed"]
    assert len(sealed) >= 2
    for info in sealed:
        frames, corrupt, trailing = validate_segment_file(info.path)
        assert corrupt is None and not trailing
        assert info.size_bytes <= 1_024 * 2  # one oversized frame may exceed the limit


@pytest.mark.asyncio
async def test_rotation_by_age_does_not_require_another_append(tmp_path: Path) -> None:
    writer = WalWriter(
        tmp_path,
        "node-1-abc",
        rotation_interval_seconds=0.05,
    )
    writer.start()
    sequence = writer.submit(make_record(0))
    await writer.flush_through(sequence)

    try:
        async def segment_was_rotated() -> bool:
            for _ in range(50):
                if any(
                    info.state == "sealed"
                    for info in iter_segment_infos(writer._directory)  # type: ignore[attr-defined]
                ):
                    return True
                await asyncio.sleep(0.01)
            return False

        assert await segment_was_rotated()
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_flush_through_times_out(tmp_path: Path) -> None:
    writer = WalWriter(tmp_path, "node-1-abc", flush_timeout_seconds=0.05)
    writer.start()
    seq = writer.submit(make_record(1))
    # sabotage the writer loop so the barrier cannot complete
    writer._closed = True  # type: ignore[attr-defined]
    writer._task.cancel()  # type: ignore[attr-defined]
    with pytest.raises(WalFlushTimeoutError):
        await writer.flush_through(seq)
    writer.close()


@pytest.mark.asyncio
async def test_writer_close_with_nonempty_queue_does_not_hang(tmp_path: Path) -> None:
    """close() with records still queued must not deadlock the writer loop
    (sentinel consumed together with the last batch)."""
    import asyncio as asyncio_mod

    writer = WalWriter(tmp_path, "node-1-abc", fsync_batch_max=256)
    writer.start()
    # submit records without flushing; then close while the queue is nonempty
    for i in range(3):
        writer.submit(make_record(i, trace_id=f"trace_{i}"))
    writer.close()
    try:
        await asyncio_mod.wait_for(writer.wait_closed(), timeout=5)
    except asyncio_mod.TimeoutError as exc:  # pragma: no cover - failure path
        raise AssertionError("wait_closed deadlocked with a non-empty queue") from exc
    infos = iter_segment_infos(writer._directory)  # type: ignore[attr-defined]
    sealed = [info for info in infos if info.state == "sealed"]
    assert len(sealed) == 1


@pytest.mark.asyncio
async def test_flush_through_blocked_by_gap(tmp_path: Path) -> None:
    """A record that could not be persisted must poison barriers covering it
    (review P1-2): flush_through must never report durability for a gap."""
    from tianzhou_agent_platform.store.observability_wal import WalGapError

    writer = WalWriter(tmp_path, "node-1-abc")
    writer.start()
    seq = writer.submit(make_record(1))
    with writer._cond:  # noqa: SLF001 - simulate a failed synchronous fallback
        writer._gap_sequences.add(seq)
    with pytest.raises(WalGapError):
        await writer.flush_through(seq)
    with pytest.raises(WalGapError):
        await writer.flush_through(seq + 10)  # any barrier covering the gap
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_registered_barrier_fails_when_earlier_gap_is_recorded(tmp_path: Path) -> None:
    """A barrier already waiting above a new gap must never be completed later."""
    from tianzhou_agent_platform.store.observability_wal import WalGapError

    writer = WalWriter(tmp_path, "node-1-abc", flush_timeout_seconds=0.2)
    writer.start()
    barrier = asyncio.create_task(writer.flush_through(2))
    await asyncio.sleep(0)

    try:
        writer._record_gap(1)  # noqa: SLF001 - simulate an append failure after registration
        with pytest.raises(WalGapError):
            await asyncio.wait_for(barrier, timeout=0.1)
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
async def test_gap_set_is_bounded_by_max_gap_sequences(tmp_path: Path) -> None:
    """During an extreme failure storm the gap set stays bounded and the
    newest gaps still poison barriers (review: cap + trim semantics)."""
    from tianzhou_agent_platform.store.observability_wal import MAX_GAP_SEQUENCES, WalGapError

    writer = WalWriter(tmp_path, "node-1-abc")
    writer.start()
    # simulate a failure storm through the production recording path
    for seq in range(1, MAX_GAP_SEQUENCES + 50):
        writer._record_gap(seq)  # noqa: SLF001 - test helper
    assert len(writer._gap_sequences) <= MAX_GAP_SEQUENCES  # noqa: SLF001 - trimmed
    # the newest gap (retained) still blocks any barrier covering it
    with pytest.raises(WalGapError):
        await writer.flush_through(MAX_GAP_SEQUENCES + 49)
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_writer_rejects_submit_after_close(tmp_path: Path) -> None:
    writer = WalWriter(tmp_path, "node-1-abc")
    writer.start()
    writer.close()
    with pytest.raises(WalError):
        writer.submit(make_record(1))


@pytest.mark.asyncio
async def test_oversized_record_is_rejected_without_stopping_writer(tmp_path: Path) -> None:
    writer = WalWriter(tmp_path, "node-1-abc")
    writer.start()
    oversized = make_record(0)
    oversized.payload = {"content": "x" * (MAX_PAYLOAD_LENGTH + 1)}

    try:
        with pytest.raises(WalError, match="record rejected before enqueue"):
            writer.submit(oversized)

        sequence_no = writer.submit(make_record(0))
        await writer.flush_through(sequence_no)
        assert sequence_no == 1
        assert writer._failure is None  # noqa: SLF001
        assert writer.metrics.telemetry_gap_count == 1
    finally:
        writer.close()
        await writer.wait_closed()


def test_producer_instance_id_shape() -> None:
    pid = build_producer_instance_id("node-7", startup_uuid="deadbeef")
    assert pid == "node-7-<pid>-deadbeef".replace("<pid>", str(os.getpid()))


def test_empty_segment_file_is_valid() -> None:
    frames, corrupt, trailing = read_frames(b"")
    assert frames == []
    assert corrupt is None
    assert not trailing


def test_metrics_snapshot_shape() -> None:
    snapshot = WalMetrics().snapshot()
    for key in (
        "obs_queue_depth",
        "obs_queue_capacity",
        "obs_wal_bytes",
        "obs_wal_segment_count",
        "obs_wal_oldest_age_seconds",
        "obs_wal_fsync_duration_ms",
        "obs_corrupt_segment_count",
    ):
        assert key in snapshot


def test_segment_append_and_seal(tmp_path: Path) -> None:
    segment = WalSegment(tmp_path / "000000000001.active")
    segment.open_append()
    segment.append(encode_frame(make_record(1)))
    segment.fsync()
    sealed = segment.seal()
    assert sealed.suffix == ".sealed"
    assert not segment.is_open
