"""Append-only WAL for reliable observability persistence.

Frame layout (documented in .docs/unibot-observability/ir-01-opentelemetry-wal-reliable-storage-design.md 7.3)::

    +------------------+------------------+----------------------+------------------+
    | Magic / Version  | Payload Length   | UTF-8 JSON Payload   | CRC32            |
    +------------------+------------------+----------------------+------------------+

Recovery rules:
  - Unsupported magic/version -> isolate the segment and report.
  - Truncated trailing frame -> truncate back to the last valid frame.
  - CRC failure in the middle -> isolate the segment, never skip and continue.
  - Replaying the same frame is made idempotent by the database UPSERT.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import re
import threading
import time
import zlib
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

MAGIC = b"UOBS"
VERSION = 1
HEADER_SIZE = 9  # 4 magic + 1 version + 4 payload length
CRC_SIZE = 4
MAX_PAYLOAD_LENGTH = 8 * 1024 * 1024  # defensive upper bound for a single frame

ACTIVE_SUFFIX = ".active"
SEALED_SUFFIX = ".sealed"
CORRUPT_SUFFIX = ".corrupt"
TMP_SUFFIX = ".tmp"
HEARTBEAT_NAME = "heartbeat"
SEGMENT_NAME_RE = re.compile(r"^(\d{12})\.(active|sealed|corrupt)$")

DEFAULT_MAX_SEGMENT_BYTES = 32 * 1024 * 1024
DEFAULT_ROTATION_INTERVAL_SECONDS = 30.0
DEFAULT_QUEUE_CAPACITY = 10_000
DEFAULT_FSYNC_BATCH_MAX = 256
DEFAULT_FLUSH_TIMEOUT_SECONDS = 10.0
DEFAULT_QUEUE_FULL_WAIT_SECONDS = 0.5
# defensive upper bound for tracked gap sequences (extreme failure storms)
MAX_GAP_SEQUENCES = 1000

RecordType = Literal["trace_started", "trace_finished", "span_started", "span_finished", "event"]


class ObsRecord(BaseModel):
    """One immutable WAL payload; the unit of reliable persistence."""

    schema_version: int = 1
    record_id: str = Field(default_factory=lambda: f"obsrec_{os.urandom(16).hex()}")
    record_type: RecordType
    producer_instance_id: str
    sequence_no: int
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str
    span_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def encode_frame(record: ObsRecord) -> bytes:
    """Serialize one record into a length-prefixed, CRC-protected frame."""
    payload = record.model_dump_json().encode("utf-8")
    if len(payload) > MAX_PAYLOAD_LENGTH:
        raise ValueError(f"WAL record payload too large: {len(payload)} bytes")
    header = MAGIC + bytes([VERSION]) + len(payload).to_bytes(4, "big")
    return header + payload + zlib.crc32(payload).to_bytes(4, "big")


@dataclass(slots=True)
class WalFrame:
    """A validated frame decoded from a segment file."""

    offset: int
    record: ObsRecord
    end_offset: int


@dataclass(slots=True)
class WalSegmentInfo:
    """A segment file found on disk."""

    path: Path
    sequence: int  # numeric part of the file name
    state: Literal["active", "sealed", "corrupt"]
    size_bytes: int
    created_at: datetime


@dataclass(slots=True)
class RecoveryResult:
    """Outcome of scanning one producer directory."""

    producer_instance_id: str
    sealed_segments: list[WalSegmentInfo] = field(default_factory=list)
    active_segments: list[WalSegmentInfo] = field(default_factory=list)
    corrupt_segments: list[WalSegmentInfo] = field(default_factory=list)
    truncated_trailing_bytes: int = 0


@dataclass(slots=True)
class WalMetrics:
    """Self-observability counters for the WAL (section 15 of the design)."""

    queue_depth: int = 0
    queue_capacity: int = 0
    wal_bytes: int = 0
    segment_count: int = 0
    oldest_segment_age_seconds: float = 0.0
    last_fsync_duration_ms: float = 0.0
    fsync_count: int = 0
    corrupt_segment_count: int = 0
    append_failure_count: int = 0
    telemetry_gap_count: int = 0
    sequence_no: int = 0
    flushed_through: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "obs_queue_depth": self.queue_depth,
            "obs_queue_capacity": self.queue_capacity,
            "obs_wal_bytes": self.wal_bytes,
            "obs_wal_segment_count": self.segment_count,
            "obs_wal_oldest_age_seconds": self.oldest_segment_age_seconds,
            "obs_wal_fsync_duration_ms": self.last_fsync_duration_ms,
            "obs_corrupt_segment_count": self.corrupt_segment_count,
            "obs_wal_append_failure_count": self.append_failure_count,
            "obs_wal_telemetry_gap_count": self.telemetry_gap_count,
            "obs_wal_sequence_no": self.sequence_no,
            "obs_wal_flushed_through": self.flushed_through,
        }


class WalError(Exception):
    """Base class for WAL failures."""


class WalFlushTimeoutError(WalError):
    """flush_through() did not complete within the configured timeout."""


class WalGapError(WalError):
    """A record could not be persisted; the WAL has a durability gap."""


class CorruptSegmentError(WalError):
    """A segment failed validation and was isolated."""


class WalSegment:
    """A single append-only segment file (`.active` while writable)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.size_bytes = 0
        self.created_at = time.monotonic()
        self._file: io.BufferedRandom | None = None

    @property
    def is_open(self) -> bool:
        return self._file is not None

    def open_append(self) -> None:
        if self._file is None:
            self._file = open(self.path, "a+b")
            self._file.seek(0, os.SEEK_END)
            self.size_bytes = self._file.tell()

    def append(self, frame: bytes) -> None:
        if self._file is None:
            raise WalError("segment is not open")
        self._file.write(frame)
        self.size_bytes += len(frame)

    def flush(self) -> None:
        if self._file is not None:
            self._file.flush()

    def fsync(self) -> None:
        if self._file is not None:
            self._file.flush()
            os.fsync(self._file.fileno())

    def close(self) -> None:
        if self._file is not None:
            try:
                self._file.close()
            finally:
                self._file = None

    def seal(self, fsync_directory: bool = True) -> Path:
        """Flush, fsync and atomically rename `.active` -> `.sealed`."""
        if self.path.suffix != ACTIVE_SUFFIX:
            raise WalError(f"cannot seal non-active segment {self.path}")
        self.flush()
        self.fsync()
        self.close()
        sealed = self.path.with_suffix(SEALED_SUFFIX)
        os.replace(self.path, sealed)
        if fsync_directory:
            _fsync_directory(sealed.parent)
        return sealed


def _fsync_directory(directory: Path) -> None:
    """Best-effort directory fsync; unsupported on some platforms (e.g. Windows)."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def iter_segment_infos(directory: Path) -> list[WalSegmentInfo]:
    """List segment files in a producer directory, ordered by sequence number."""
    if not directory.is_dir():
        return []
    infos: list[WalSegmentInfo] = []
    for path in directory.iterdir():
        match = SEGMENT_NAME_RE.match(path.name)
        if match is None or not path.is_file():
            continue
        stat = path.stat()
        created_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        infos.append(
            WalSegmentInfo(
                path=path,
                sequence=int(match.group(1)),
                state=cast(Literal["active", "sealed", "corrupt"], match.group(2)),
                size_bytes=stat.st_size,
                created_at=created_at,
            )
        )
    return sorted(infos, key=lambda info: info.sequence)


def read_frames(data: bytes) -> tuple[list[WalFrame], int | None, bool]:
    """Decode frames from raw segment bytes.

    Returns ``(frames, corrupt_offset, trailing_truncated)``:

    - ``corrupt_offset`` is set when the corruption is *inside* the file
      (bad magic/version/length/CRC followed by more bytes) -> isolate.
    - ``trailing_truncated`` is set when the file ends in the middle of a
      frame -> truncate back to the last valid frame.
    """
    frames: list[WalFrame] = []
    offset = 0
    while offset < len(data):
        remaining = len(data) - offset
        if remaining < HEADER_SIZE:
            return frames, None, True
        if data[offset : offset + 4] != MAGIC:
            return frames, offset, False
        if data[offset + 4] != VERSION:
            return frames, offset, False
        length = int.from_bytes(data[offset + 5 : offset + 9], "big")
        if length > MAX_PAYLOAD_LENGTH or length < 0:
            return frames, offset, False
        if remaining < HEADER_SIZE + length + CRC_SIZE:
            return frames, None, True
        payload = data[offset + HEADER_SIZE : offset + HEADER_SIZE + length]
        stored_crc = int.from_bytes(
            data[offset + HEADER_SIZE + length : offset + HEADER_SIZE + length + CRC_SIZE],
            "big",
        )
        if zlib.crc32(payload) != stored_crc:
            return frames, offset, False
        try:
            record = ObsRecord.model_validate_json(payload)
        except Exception as exc:  # noqa: BLE001 - invalid payload means corruption
            logger.warning("WAL frame at offset %d failed JSON validation: %s", offset, exc)
            return frames, offset, False
        frames.append(WalFrame(offset=offset, record=record, end_offset=offset + HEADER_SIZE + length + CRC_SIZE))
        offset += HEADER_SIZE + length + CRC_SIZE
    return frames, None, False


def validate_segment_file(path: Path) -> tuple[list[WalFrame], int | None, bool]:
    """Read and validate a segment file on disk.

    Defensive size bound: segments rotate at 32 MiB; anything far beyond that
    is treated as corruption instead of being read into memory unboundedly.
    """
    stat = path.stat()
    if stat.st_size > MAX_PAYLOAD_LENGTH * 32:
        return [], 0, False
    data = path.read_bytes()
    return read_frames(data)


class WalRecovery:
    """Startup scan: validate frames, truncate half-written tails, isolate corruption."""

    def __init__(self, wal_root: Path) -> None:
        self.wal_root = wal_root

    def scan(self, producer_instance_id: str) -> RecoveryResult:
        """Validate every segment of one producer directory.

        Mutates the directory only when needed: truncates trailing garbage on
        the last segment and renames corrupt segments to ``*.corrupt``.
        """
        directory = self.wal_root / producer_instance_id
        result = RecoveryResult(producer_instance_id=producer_instance_id)
        infos = iter_segment_infos(directory)
        for index, info in enumerate(infos):
            is_last = index == len(infos) - 1
            frames, corrupt_offset, trailing = validate_segment_file(info.path)
            if corrupt_offset is not None:
                self._isolate(info.path)
                result.corrupt_segments.append(info)
                logger.error("WAL segment %s corrupted at offset %d; isolated", info.path, corrupt_offset)
                continue
            if trailing and is_last:
                last_end = frames[-1].end_offset if frames else 0
                if last_end < info.size_bytes:
                    _truncate_file(info.path, last_end)
                    result.truncated_trailing_bytes += info.size_bytes - last_end
                    logger.warning(
                        "WAL segment %s had %d trailing bytes; truncated to last valid frame",
                        info.path,
                        info.size_bytes - last_end,
                    )
                elif last_end == 0 and info.size_bytes == 0:
                    pass  # empty active segment is legal
            if trailing and not is_last:
                # A non-last segment with trailing garbage is corruption.
                self._isolate(info.path)
                result.corrupt_segments.append(info)
                logger.error("WAL segment %s has trailing garbage but is not the last; isolated", info.path)
                continue
            if info.state == "sealed":
                result.sealed_segments.append(info)
            elif info.state == "active":
                result.active_segments.append(info)
            elif info.state == "corrupt":
                result.corrupt_segments.append(info)
        return result

    @staticmethod
    def _isolate(path: Path) -> None:
        corrupt = path.with_suffix(CORRUPT_SUFFIX)
        os.replace(path, corrupt)
        _fsync_directory(path.parent)


def _truncate_file(path: Path, size: int) -> None:
    with open(path, "r+b") as file:
        file.truncate(size)
        file.flush()
        os.fsync(file.fileno())
    _fsync_directory(path.parent)


class WalWriter:
    """Single-writer WAL appender with bounded queue, batched fsync and barriers.

    Responsibilities (design section 7.4):

    - single writer model, no file locking
    - bounded queue so MySQL failures cannot grow process memory unbounded
    - multiple records merged into one write + fsync
    - awaitable completion for ``flush_through(sequence_no)``
    - records are only published to the ingest side after fsync succeeds
    - barrier is never notified on WAL write failure

    ``submit()`` is synchronous on purpose: OTel ``SpanProcessor.on_end`` is a
    synchronous callback. The queue uses ``threading.Condition`` so the
    single-writer loop (running in a worker thread via ``asyncio.to_thread``)
    and synchronous callers never race. When the queue is full we wait briefly
    (design 7.5); on timeout a controlled synchronous append drains the queue
    under the same file lock, preserving monotonic frame order by
    ``sequence_no``.
    """

    def __init__(
        self,
        wal_root: Path,
        producer_instance_id: str,
        *,
        max_segment_bytes: int = DEFAULT_MAX_SEGMENT_BYTES,
        rotation_interval_seconds: float = DEFAULT_ROTATION_INTERVAL_SECONDS,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
        fsync_batch_max: int = DEFAULT_FSYNC_BATCH_MAX,
        flush_timeout_seconds: float = DEFAULT_FLUSH_TIMEOUT_SECONDS,
        queue_full_wait_seconds: float = DEFAULT_QUEUE_FULL_WAIT_SECONDS,
        on_records_flushed: Callable[[list[ObsRecord]], Awaitable[None]] | None = None,
    ) -> None:
        self.wal_root = wal_root
        self.producer_instance_id = producer_instance_id
        self.max_segment_bytes = max_segment_bytes
        self.rotation_interval_seconds = rotation_interval_seconds
        self.fsync_batch_max = fsync_batch_max
        self.flush_timeout_seconds = flush_timeout_seconds
        self.queue_full_wait_seconds = queue_full_wait_seconds
        self.on_records_flushed = on_records_flushed
        self.metrics = WalMetrics(queue_capacity=queue_capacity)
        self._directory = wal_root / producer_instance_id
        self._pending: deque[ObsRecord | None] = deque()
        self._cond = threading.Condition()
        # Serializes every file mutation (writer batches and synchronous
        # fallback appends) so frame order stays monotonic by sequence_no.
        self._file_lock = threading.Lock()
        self._writer_processing = False
        self._sequence_no = 0
        self._flushed_through = 0
        self._waiters: dict[int, list[asyncio.Future[None]]] = {}
        # sequence numbers whose record could NOT be persisted (sync fallback
        # skipped/failed); barriers covering them must never report success
        self._gap_sequences: set[int] = set()
        self._segment: WalSegment | None = None
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._closed = False
        self._last_heartbeat_at = 0.0

    @property
    def sequence_no(self) -> int:
        return self._sequence_no

    @property
    def flushed_through(self) -> int:
        return self._flushed_through

    @property
    def queue_depth(self) -> int:
        with self._cond:
            return len(self._pending)

    def start(self) -> None:
        if self._task is not None:
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        recovery = WalRecovery(self.wal_root).scan(self.producer_instance_id)
        self.metrics.corrupt_segment_count += len(recovery.corrupt_segments)
        self._segment = self._open_active_segment(recovery.active_segments)
        # liveness heartbeat: other instances claim orphaned .active segments
        # only when this file is missing or stale (review round 2, P1-1).
        # The heartbeat is refreshed by an independent task so a blocked
        # MySQL publish can never make a live producer look dead
        # (review round 3, P1).
        self._touch_heartbeat()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"wal-heartbeat-{self.producer_instance_id}"
        )
        self._task = asyncio.create_task(self._run(), name=f"wal-writer-{self.producer_instance_id}")

    @property
    def heartbeat_path(self) -> Path:
        return self._directory / HEARTBEAT_NAME

    def _touch_heartbeat(self) -> None:
        try:
            self.heartbeat_path.write_text(
                datetime.now(timezone.utc).isoformat(), encoding="utf-8"
            )
        except OSError:
            logger.exception("WAL heartbeat update failed")

    def _open_active_segment(self, active_segments: list[WalSegmentInfo]) -> WalSegment:
        if active_segments:
            # seal any earlier leftovers, resume appending the last one
            for info in active_segments[:-1]:
                try:
                    WalSegment(info.path).seal()
                except OSError:
                    logger.exception("Failed to seal leftover active segment %s", info.path)
            segment = WalSegment(active_segments[-1].path)
            segment.open_append()
            return segment
        return self._new_segment()

    def _new_segment(self) -> WalSegment:
        sequence = self._next_segment_sequence()
        path = self._directory / f"{sequence:012d}{ACTIVE_SUFFIX}"
        segment = WalSegment(path)
        segment.open_append()
        return segment

    def _next_segment_sequence(self) -> int:
        infos = iter_segment_infos(self._directory)
        return (infos[-1].sequence + 1) if infos else 1

    def submit(self, record: ObsRecord) -> int:
        """Enqueue a record and return its sequence number (thread-safe, sync).

        Safe to call from synchronous callbacks such as OTel
        ``SpanProcessor.on_end``. On a full queue we wait up to
        ``queue_full_wait_seconds`` for the writer to drain, then fall back to
        a controlled synchronous append (design 7.5).
        """
        if self._closed:
            raise WalError("WAL writer is closed")
        self._sequence_no += 1
        record.sequence_no = self._sequence_no
        with self._cond:
            if len(self._pending) < self.metrics.queue_capacity:
                self._pending.append(record)
                self._cond.notify()
                return record.sequence_no
        # queue full: wait briefly for the writer to drain
        deadline = time.monotonic() + self.queue_full_wait_seconds
        with self._cond:
            while len(self._pending) >= self.metrics.queue_capacity and time.monotonic() < deadline:
                self._cond.wait(timeout=0.01)
            if len(self._pending) < self.metrics.queue_capacity:
                self._pending.append(record)
                self._cond.notify()
                return record.sequence_no
        logger.warning("WAL queue still full after %.2fs; synchronous fallback append", self.queue_full_wait_seconds)
        return self._submit_with_sync_fallback(record)

    def _submit_with_sync_fallback(self, record: ObsRecord) -> int:
        # Wait until the writer finishes its in-flight batch so file order
        # stays monotonic, then drain and append synchronously.
        deadline = time.monotonic() + self.queue_full_wait_seconds
        with self._cond:
            while self._writer_processing and time.monotonic() < deadline:
                self._cond.wait(timeout=0.01)
            if self._writer_processing:
                # Never drop the record: put it back on the queue so the
                # writer persists it once it catches up (the barrier will
                # wait for it). Only genuine write failures are gaps.
                with self._cond:
                    self._pending.append(record)
                    self._cond.notify()
                logger.warning(
                    "WAL sync fallback deferred (writer busy); record %d requeued", record.sequence_no
                )
                return record.sequence_no
            pending = [item for item in self._pending if item is not None]
            if pending:
                self._pending.clear()
        pending.sort(key=lambda item: item.sequence_no)
        pending.append(record)
        try:
            with self._file_lock:
                self._append_batch_locked(pending)
        except Exception as exc:  # noqa: BLE001 - report and continue
            logger.exception("Synchronous WAL fallback append failed")
            self.metrics.append_failure_count += 1
            self.metrics.telemetry_gap_count += 1
            self._record_gap(record.sequence_no)
            failure = WalError("WAL synchronous append failed")
            failure.__cause__ = exc
            self._fail_waiters_through(record.sequence_no, failure)
        return record.sequence_no

    def _record_gap(self, sequence_no: int) -> None:
        """Track a lost sequence number; capped so an extreme failure storm
        cannot grow the set unboundedly (review)."""
        with self._cond:
            self._gap_sequences.add(sequence_no)
            if len(self._gap_sequences) > MAX_GAP_SEQUENCES:
                for stale in sorted(self._gap_sequences)[: len(self._gap_sequences) - MAX_GAP_SEQUENCES]:
                    self._gap_sequences.discard(stale)

    async def flush_through(self, sequence_no: int) -> None:
        """Wait until every record up to ``sequence_no`` has been fsynced.

        Never reports success when a tracked gap (a record that could not be
        persisted) exists at or below ``sequence_no`` (review P1-2). Gap
        tracking is capped at ``MAX_GAP_SEQUENCES``: during an extreme
        failure storm the oldest gaps are dropped (they remain observable via
        ``append_failure_count``/``telemetry_gap_count``), so a barrier may
        only be released past an *untracked* stale gap in that pathological
        case; business barriers always pass the current maximum sequence
        number, which covers the newest (retained) gaps.
        """
        with self._cond:
            covering_gap = any(gap <= sequence_no for gap in self._gap_sequences)
        if covering_gap:
            raise WalGapError(
                f"WAL flush_through({sequence_no}) blocked by a telemetry gap; data is not durable"
            )
        if sequence_no <= self._flushed_through:
            return
        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        self._waiters.setdefault(sequence_no, []).append(future)
        try:
            await asyncio.wait_for(future, timeout=self.flush_timeout_seconds)
        except TimeoutError as exc:
            # the writer may have already popped and resolved the future;
            # only remove it if it is still registered
            futures = self._waiters.get(sequence_no)
            if futures is not None and future in futures:
                futures.remove(future)
                if not futures:
                    self._waiters.pop(sequence_no, None)
            raise WalFlushTimeoutError(
                f"WAL flush_through({sequence_no}) timed out after {self.flush_timeout_seconds}s"
            ) from exc

    def close(self) -> None:
        """Request graceful shutdown: drain, fsync and seal the active segment."""
        if self._closed:
            return
        with self._cond:
            self._closed = True
            self._pending.append(None)  # sentinel
            self._cond.notify()

    async def wait_closed(self) -> None:
        # keep the heartbeat alive until the writer has drained and sealed
        # the active segment: a premature heartbeat stop could let other
        # instances claim a segment that is still being sealed during a slow
        # shutdown (review round 4, P1)
        if self._task is not None:
            await self._task
            self._task = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._heartbeat_task = None

    async def _run(self) -> None:
        try:
            while True:
                batch = await asyncio.to_thread(self._take_batch)
                if batch is None:
                    break
                await asyncio.to_thread(self._append_batch_sync, batch)
                self._maybe_heartbeat()
                await self._publish(batch)
            # graceful shutdown: everything already drained; seal the segment
            if self._segment is not None and self._segment.size_bytes > 0:
                self._segment.seal()
            elif self._segment is not None:
                self._segment.close()
        except Exception:
            logger.exception("WAL writer crashed")
            # stop the liveness heartbeat too: a dead writer must not keep
            # its producer looking alive, or orphaned segments would never
            # be claimed by other instances (review round 3)
            if self._heartbeat_task is not None:
                self._heartbeat_task.cancel()
            self._fail_all_waiters()

    def _maybe_heartbeat(self) -> None:
        """Refresh the liveness heartbeat at most every 10 seconds."""
        now = time.monotonic()
        if now - self._last_heartbeat_at >= 10.0:
            self._last_heartbeat_at = now
            self._touch_heartbeat()

    async def _heartbeat_loop(self) -> None:
        """Independent liveness task: never blocked by WAL writes or the
        ingest publish callback, and only stops on explicit cancellation —
        close() alone must not stop it before the writer drains and seals
        (review round 3/4, P1)."""
        try:
            while True:
                await asyncio.sleep(10.0)
                self._maybe_heartbeat()
        except asyncio.CancelledError:
            pass

    def _take_batch(self) -> list[ObsRecord] | None:
        """Block until records or the shutdown sentinel are available."""
        with self._cond:
            while not self._pending and not self._closed:
                self._cond.wait(timeout=1.0)
            batch: list[ObsRecord] = []
            while self._pending and len(batch) < self.fsync_batch_max:
                item = self._pending.popleft()
                if item is None:
                    self._closed = True  # sentinel received
                    break
                batch.append(item)
            if batch:
                self._writer_processing = True
                self.metrics.queue_depth = len(self._pending)
                return batch
            # only the sentinel was present (or closed with an empty queue)
            return None

    def _append_batch_sync(self, batch: list[ObsRecord]) -> None:
        with self._file_lock:
            try:
                self._append_batch_locked(batch)
            finally:
                with self._cond:
                    self._writer_processing = False
                    self._cond.notify_all()

    def _append_batch_locked(self, batch: list[ObsRecord]) -> None:
        if self._segment is None:
            raise WalError("no active segment")
        self._maybe_rotate(self._segment)
        started = time.perf_counter()
        for record in batch:
            frame = encode_frame(record)
            if self._segment.size_bytes + len(frame) > self.max_segment_bytes:
                self._maybe_rotate(self._segment)
            self._segment.append(frame)
        self._segment.fsync()
        self.metrics.last_fsync_duration_ms = (time.perf_counter() - started) * 1000.0
        self.metrics.fsync_count += 1
        self.metrics.wal_bytes = self._segment.size_bytes
        self.metrics.sequence_no = batch[-1].sequence_no
        self._flushed_through = max(self._flushed_through, batch[-1].sequence_no)
        self.metrics.flushed_through = self._flushed_through
        self._notify_waiters_through(self._flushed_through)

    async def _publish(self, batch: list[ObsRecord]) -> None:
        # Records are only published to the ingest side after fsync succeeded.
        if self.on_records_flushed is None:
            return
        try:
            await self.on_records_flushed(batch)
        except Exception:
            logger.exception("WAL record publish callback failed")

    def _maybe_rotate(self, segment: WalSegment) -> None:
        age = time.monotonic() - segment.created_at
        should_rotate = (
            segment.size_bytes >= self.max_segment_bytes
            or age >= self.rotation_interval_seconds
        )
        if not should_rotate:
            return
        try:
            sealed = segment.seal()
            logger.info("WAL segment rotated: %s", sealed.name)
        except OSError:
            logger.exception("WAL segment rotation failed; continuing with current segment")
            return
        self._segment = self._new_segment()

    def _notify_waiters_through(self, sequence_no: int) -> None:
        resolved = [key for key in self._waiters if key <= sequence_no]
        for key in resolved:
            for future in self._waiters.pop(key):
                if not future.done():
                    future.get_loop().call_soon_threadsafe(future.set_result, None)

    def _fail_waiters_through(self, sequence_no: int, exc: Exception) -> None:
        resolved = [key for key in self._waiters if key <= sequence_no]
        for key in resolved:
            for future in self._waiters.pop(key):
                if not future.done():
                    future.get_loop().call_soon_threadsafe(future.set_exception, exc)

    def _fail_all_waiters(self) -> None:
        for futures in self._waiters.values():
            for future in futures:
                if not future.done():
                    future.get_loop().call_soon_threadsafe(future.set_exception, WalError("WAL writer stopped"))
        self._waiters.clear()


def build_producer_instance_id(node_id: str, process_id: int | None = None, startup_uuid: str | None = None) -> str:
    """``<node_id>-<process_id>-<startup_uuid>`` (design section 7.1)."""
    import uuid

    process = process_id if process_id is not None else os.getpid()
    startup = startup_uuid if startup_uuid is not None else uuid.uuid4().hex
    return f"{node_id}-{process}-{startup}"
