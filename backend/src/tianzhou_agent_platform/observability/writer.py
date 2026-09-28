"""Writer/Worker coordination: bounded ingest of fsynced WAL records into OBS
MySQL, sealed-segment scanning/replay and segment cleanup.

Design (ir-01 sections 10.3, 13.2, 15):

- Records are published to the ingest side only after WAL fsync succeeds
  (WalWriter calls ``on_records_flushed``); those calls are best-effort and
  failures are safe because the data stays in the segment files.
- A periodic scanner replays every ``.sealed`` segment (including orphaned
  segments of other producer instances) with idempotent UPSERTs, then deletes
  the segment. Crash between commit and delete -> replay -> UPSERT no-op.
- Segments are claimed by an atomic rename to ``.ingesting`` so multiple
  Backend instances never replay the same file; the database UPSERT remains
  the final correctness guarantee. A leftover ``.ingesting`` after a crash is
  renamed back to ``.sealed`` and replayed.
- Ingest metrics (section 15) are exposed via ``snapshot()``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tianzhou_agent_platform.store.observability_store import ObservabilityStore
from tianzhou_agent_platform.store.observability_wal import (
    HEARTBEAT_NAME,
    ObsRecord,
    SEALED_SUFFIX,
    WalSegmentInfo,
    iter_segment_infos,
    validate_segment_file,
)

logger = logging.getLogger(__name__)

INGESTING_SUFFIX = ".ingesting"

DEFAULT_SCAN_INTERVAL_SECONDS = 2.0
DEFAULT_BATCH_MAX = 500
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_LIVE_QUEUE_CAPACITY = 1_000
# an .active segment of another producer that has not been written for this
# long is considered orphaned (crashed process) and gets sealed + replayed
DEFAULT_ORPHAN_ACTIVE_MIN_AGE_SECONDS = 60.0


@dataclass(slots=True)
class IngestMetrics:
    ingest_batch_size: int = 0
    ingest_duration_ms: float = 0.0
    ingest_last_success_at: datetime | None = None
    ingest_retry_count: int = 0
    ingest_segments_consumed: int = 0
    ingest_records_consumed: int = 0
    ingest_failure_count: int = 0
    segments_pending: int = 0
    telemetry_gap_count: int = 0
    wal_total_bytes: int = 0
    wal_water_level: str = "ok"
    retention_deleted: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "obs_ingest_batch_size": self.ingest_batch_size,
            "obs_ingest_duration_ms": self.ingest_duration_ms,
            "obs_ingest_last_success_at": (
                self.ingest_last_success_at.isoformat() if self.ingest_last_success_at else None
            ),
            "obs_ingest_retry_count": self.ingest_retry_count,
            "obs_ingest_segments_consumed": self.ingest_segments_consumed,
            "obs_ingest_records_consumed": self.ingest_records_consumed,
            "obs_ingest_failure_count": self.ingest_failure_count,
            "obs_segments_pending": self.segments_pending,
            "obs_telemetry_gap_count": self.telemetry_gap_count,
            "obs_wal_total_bytes": self.wal_total_bytes,
            "obs_wal_water_level": self.wal_water_level,
            "obs_retention_deleted": self.retention_deleted,
        }


class ObsIngestWorker:
    """Batches fsynced WAL records into OBS MySQL and recycles segments."""

    def __init__(
        self,
        wal_root: Path,
        store: ObservabilityStore,
        producer_instance_id: str,
        *,
        scan_interval_seconds: float = DEFAULT_SCAN_INTERVAL_SECONDS,
        batch_max: int = DEFAULT_BATCH_MAX,
        live_queue_capacity: int = DEFAULT_LIVE_QUEUE_CAPACITY,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        orphan_active_min_age_seconds: float = DEFAULT_ORPHAN_ACTIVE_MIN_AGE_SECONDS,
        wal_max_bytes: int | None = None,
        retention_days: int | None = None,
        raw_root: Path | None = None,
    ) -> None:
        self.wal_root = wal_root
        self.store = store
        self.producer_instance_id = producer_instance_id
        self.scan_interval_seconds = scan_interval_seconds
        self.batch_max = batch_max
        self.retry_backoff_seconds = retry_backoff_seconds
        self.orphan_active_min_age_seconds = orphan_active_min_age_seconds
        self.wal_max_bytes = wal_max_bytes
        self.retention_days = retention_days
        self.raw_root = raw_root
        self.metrics = IngestMetrics()
        self._stop = asyncio.Event()
        self._live_queue: asyncio.Queue[list[ObsRecord]] = asyncio.Queue(
            maxsize=live_queue_capacity
        )
        self._task: asyncio.Task[None] | None = None
        self._last_cleanup_at = 0.0

    async def on_records_flushed(self, records: list[ObsRecord]) -> None:
        """Live ingest callback invoked by WalWriter after fsync (section 7.4).

        This callback deliberately performs no database I/O. WalWriter awaits
        it immediately after fsync, so doing the UPSERT here would let a slow
        MySQL connection block later WAL batches and their durability barriers.
        A full queue is safe: the records remain in WAL and sealed-segment
        replay is the fallback.
        """
        if not records:
            return
        try:
            self._live_queue.put_nowait(list(records))
        except asyncio.QueueFull:
            self.metrics.ingest_retry_count += 1
            logger.warning(
                "Live ingest queue is full; %d WAL records will be recovered by segment replay",
                len(records),
            )

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run(), name="obs-ingest-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        await self._task
        self._task = None

    async def _run(self) -> None:
        retry_batch: list[ObsRecord] | None = None
        next_scan_at = 0.0
        try:
            await self._recover_claiming_segments()
            while not self._stop.is_set():
                now = time.monotonic()
                if now >= next_scan_at:
                    try:
                        await self._scan_and_replay()
                    except Exception:
                        logger.exception("OBS ingest scan failed")
                    try:
                        await self._reconcile_interrupted_producers()
                    except Exception:
                        logger.exception("OBS interrupted-producer reconciliation failed")
                    try:
                        await self._check_retention_cleanup()
                    except Exception:
                        logger.exception("OBS retention cleanup failed")
                    next_scan_at = time.monotonic() + self.scan_interval_seconds

                if retry_batch is not None:
                    try:
                        await asyncio.wait_for(
                            self._stop.wait(), timeout=self.retry_backoff_seconds
                        )
                        break
                    except TimeoutError:
                        batch = retry_batch
                else:
                    timeout = max(0.0, next_scan_at - time.monotonic())
                    try:
                        batch = await asyncio.wait_for(
                            self._live_queue.get(), timeout=timeout
                        )
                    except TimeoutError:
                        continue

                try:
                    await self._ingest_batch(batch)
                except Exception:
                    retry_batch = batch
                    self.metrics.ingest_retry_count += 1
                    self.metrics.ingest_failure_count += 1
                    logger.warning(
                        "Live ingest of %d WAL records failed; retrying in %.2fs",
                        len(batch),
                        self.retry_backoff_seconds,
                        exc_info=True,
                    )
                else:
                    retry_batch = None
                    self._live_queue.task_done()
        finally:
            # drain any remaining sealed segments on shutdown (best effort)
            try:
                await self._scan_and_replay()
            except Exception:
                logger.exception("OBS ingest final drain failed")

    async def _check_retention_cleanup(self) -> None:
        """Run the retention cleanup at most once per day (review P2-2)."""
        if self.retention_days is None or self.retention_days <= 0:
            return
        now = time.monotonic()
        if now - self._last_cleanup_at < 24 * 3600:
            return
        self._last_cleanup_at = now
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        deleted = await self.store.delete_older_than(cutoff)
        self.metrics.retention_deleted += sum(deleted.values())
        logger.info("OBS retention cleanup removed rows older than %s: %s", cutoff.date(), deleted)
        await self._cleanup_raw_files(cutoff)

    async def _reconcile_interrupted_producers(self) -> None:
        reconcile = getattr(self.store, "fail_interrupted_producers", None)
        if reconcile is None or not self.wal_root.is_dir():
            return
        stale: list[str] = []
        now = time.time()
        for producer_dir in self.wal_root.iterdir():
            if not producer_dir.is_dir() or producer_dir.name == self.producer_instance_id:
                continue
            heartbeat = producer_dir / HEARTBEAT_NAME
            try:
                heartbeat_age = now - (await asyncio.to_thread(heartbeat.stat)).st_mtime
            except OSError:
                mtimes: list[float] = []
                for path in producer_dir.iterdir():
                    try:
                        mtimes.append((await asyncio.to_thread(path.stat)).st_mtime)
                    except OSError:
                        continue
                producer_age = now - max(mtimes) if mtimes else float("inf")
            else:
                producer_age = heartbeat_age
            if (
                self.orphan_active_min_age_seconds <= 0
                or producer_age >= self.orphan_active_min_age_seconds
            ):
                stale.append(producer_dir.name)
        if not stale:
            return
        counts = await reconcile(stale, interrupted_at=datetime.now(timezone.utc))
        if sum(counts.values()):
            logger.warning(
                "Marked interrupted OBS rows failed for %d stale producers: %s",
                len(stale),
                counts,
            )

    async def _cleanup_raw_files(self, cutoff: datetime) -> None:
        if self.raw_root is None or not self.raw_root.is_dir():
            return
        removed = 0
        for tenant in self.raw_root.iterdir():
            if not tenant.is_dir():
                continue
            for user in tenant.iterdir():
                if not user.is_dir():
                    continue
                for trace_dir in user.iterdir():
                    try:
                        mtime = (await asyncio.to_thread(trace_dir.stat)).st_mtime
                    except OSError:
                        continue
                    if datetime.fromtimestamp(mtime, tz=timezone.utc) >= cutoff:
                        continue
                    # only delete raw files whose trace row is already gone;
                    # an active trace directory must not be removed while its
                    # DB row still exists, and a query failure must be treated
                    # conservatively (skip, never delete) (review round 2)
                    try:
                        row = await self.store.get_trace(trace_dir.name)
                    except Exception:  # noqa: BLE001 - cleanup must not break the loop
                        continue
                    if row is not None:
                        continue
                    await asyncio.to_thread(shutil.rmtree, trace_dir, ignore_errors=True)
                    removed += 1
        if removed:
            logger.info("OBS retention cleanup removed %d raw IO trace directories", removed)

    async def _recover_claiming_segments(self) -> None:
        """Rename leftover ``.ingesting`` files back to ``.sealed`` after a crash."""
        if not self.wal_root.is_dir():
            return
        for producer_dir in self.wal_root.iterdir():
            if not producer_dir.is_dir():
                continue
            for path in producer_dir.iterdir():
                if path.name.endswith(INGESTING_SUFFIX):
                    sealed = path.with_suffix(SEALED_SUFFIX)
                    try:
                        await asyncio.to_thread(os.replace, path, sealed)
                        logger.info("Recovered claiming segment %s -> %s", path.name, sealed.name)
                    except OSError:
                        logger.exception("Failed to recover claiming segment %s", path)

    async def _scan_and_replay(self) -> None:
        if not self.wal_root.is_dir():
            return
        pending = 0
        wal_total_bytes = 0
        for producer_dir in sorted(self.wal_root.iterdir(), key=lambda p: p.name):
            if not producer_dir.is_dir():
                continue
            is_own_dir = producer_dir.name == self.producer_instance_id
            for info in iter_segment_infos(producer_dir):
                wal_total_bytes += info.size_bytes
                if info.state == "sealed":
                    pending += 1
                    await self._replay_segment(producer_dir, info.path)
                elif info.state == "active" and not is_own_dir:
                    # Orphaned .active left by a crashed producer: after the
                    # age threshold, seal it and replay it like any sealed
                    # segment (review P1-1). Live producers rotate within 30s,
                    # so a stale active is a crash leftover.
                    if await self._claim_orphan_active(info):
                        pending += 1
                        await self._replay_segment(
                            producer_dir,
                            info.path.with_suffix(SEALED_SUFFIX),
                        )
        self.metrics.segments_pending = pending
        self.metrics.wal_total_bytes = wal_total_bytes
        self._check_wal_water_level(wal_total_bytes)

    def _check_wal_water_level(self, wal_total_bytes: int) -> None:
        """70/85/95% water levels (design section 14, review P2-2)."""
        if self.wal_max_bytes is None or self.wal_max_bytes <= 0:
            return
        ratio = wal_total_bytes / self.wal_max_bytes
        if ratio >= 0.95:
            self.metrics.wal_water_level = "overflow"
            logger.error(
                "WAL usage at %.0f%% of the %d-byte cap; direct MySQL write may be needed", ratio * 100, self.wal_max_bytes
            )
        elif ratio >= 0.85:
            self.metrics.wal_water_level = "critical"
            logger.warning("WAL usage at %.0f%% of the cap", ratio * 100)
        elif ratio >= 0.70:
            self.metrics.wal_water_level = "warning"
            logger.info("WAL usage at %.0f%% of the cap", ratio * 100)
        else:
            self.metrics.wal_water_level = "ok"

    async def _claim_orphan_active(self, info: WalSegmentInfo) -> bool:
        producer_dir = info.path.parent
        # liveness check: a fresh heartbeat means the producer is still
        # alive (idle instances keep their heartbeat current), so its
        # segments must never be claimed (review round 2, P1-1).
        heartbeat = producer_dir / HEARTBEAT_NAME
        try:
            heartbeat_age = time.time() - (await asyncio.to_thread(heartbeat.stat)).st_mtime
        except OSError:
            heartbeat_age = None  # no heartbeat file -> assume crashed
        if (
            self.orphan_active_min_age_seconds > 0
            and heartbeat_age is not None
            and heartbeat_age < self.orphan_active_min_age_seconds
        ):
            return False
        try:
            stat = await asyncio.to_thread(info.path.stat)
        except OSError:
            return False
        # st_mtime is wall-clock (epoch); compare with time.time()
        if (
            self.orphan_active_min_age_seconds > 0
            and time.time() - stat.st_mtime < self.orphan_active_min_age_seconds
        ):
            return False
        # double-check the segment did not grow during the age check: a live
        # producer writes periodically, so growth means it is still active
        # (review: low-traffic live segments must not be claimed)
        await asyncio.sleep(0.5)
        try:
            second = await asyncio.to_thread(info.path.stat)
        except OSError:
            return False
        if second.st_size != stat.st_size or second.st_mtime != stat.st_mtime:
            return False
        sealed = info.path.with_suffix(SEALED_SUFFIX)
        try:
            await asyncio.to_thread(os.replace, info.path, sealed)
            logger.info("Claimed orphaned active segment %s -> %s", info.path.name, sealed.name)
            return True
        except OSError:
            return False

    async def _replay_segment(self, producer_dir: Path, sealed_path: Path) -> None:
        claimed = sealed_path.with_suffix(INGESTING_SUFFIX)
        try:
            # atomic claim: only one instance ever replays this segment
            await asyncio.to_thread(os.replace, sealed_path, claimed)
        except FileNotFoundError:
            return  # another instance claimed it already
        except OSError:
            logger.exception("Failed to claim segment %s", sealed_path)
            return
        try:
            frames, corrupt_offset, trailing = await asyncio.to_thread(validate_segment_file, claimed)
            if corrupt_offset is not None:
                corrupt = claimed.with_suffix(".corrupt")
                await asyncio.to_thread(os.replace, claimed, corrupt)
                logger.error("OBS segment %s corrupted at offset %d; isolated", claimed, corrupt_offset)
                return
            records = [frame.record for frame in frames]
            for start in range(0, len(records), self.batch_max):
                batch = records[start : start + self.batch_max]
                await self._ingest_batch(batch)
            await asyncio.to_thread(claimed.unlink)
            self.metrics.ingest_segments_consumed += 1
            self.metrics.ingest_records_consumed += len(records)
            logger.debug("OBS segment %s ingested (%d records) and deleted", claimed.name, len(records))
        except Exception:
            # crash-safe: rename back so the next scan replays the segment
            try:
                await asyncio.to_thread(os.replace, claimed, sealed_path)
            except OSError:
                logger.exception("Failed to restore segment %s after ingest failure", claimed)
            self.metrics.ingest_retry_count += 1
            self.metrics.ingest_failure_count += 1
            logger.warning("OBS segment %s ingest failed; will retry", claimed.name, exc_info=True)

    async def _ingest_batch(self, records: list[ObsRecord]) -> None:
        started = time.perf_counter()
        await self.store.bulk_upsert(records)
        self.metrics.ingest_batch_size = len(records)
        self.metrics.ingest_duration_ms = (time.perf_counter() - started) * 1000.0
        self.metrics.ingest_last_success_at = datetime.now(timezone.utc)
