"""Redis Streams consumer that projects durable OBS records into MySQL."""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from redis import asyncio as redis_async
from redis.exceptions import ResponseError

from tianzhou_agent_platform.core.observation_context import suppress_observation
from tianzhou_agent_platform.store.observability_buffer import ObsBufferError, ObsRecord
from tianzhou_agent_platform.store.observability_redis import (
    DEFAULT_DURABILITY_TIMEOUT_MS,
    DEFAULT_PRODUCERS_KEY,
    DEFAULT_STREAM_KEY,
)
from tianzhou_agent_platform.store.observability_store import ObservabilityStore

logger = logging.getLogger(__name__)

DEFAULT_GROUP_NAME = "unibot-obs-mysql-v1"
DEFAULT_DLQ_KEY = "unibot:obs:records:dlq:v1"
DEFAULT_BATCH_MAX = 500
DEFAULT_BLOCK_MS = 1_000
DEFAULT_CLAIM_IDLE_MS = 60_000
DEFAULT_MAINTENANCE_INTERVAL_SECONDS = 5.0
DEFAULT_PRODUCER_STALE_SECONDS = 120.0
DEFAULT_DLQ_MAXLEN = 10_000


@dataclass(slots=True)
class RedisIngestMetrics:
    ingest_batch_size: int = 0
    ingest_duration_ms: float = 0.0
    ingest_last_success_at: datetime | None = None
    ingest_retry_count: int = 0
    ingest_records_consumed: int = 0
    ingest_failure_count: int = 0
    reclaimed_records: int = 0
    invalid_records: int = 0
    dlq_records: int = 0
    stream_length: int = 0
    consumer_pending: int = 0
    consumer_lag: int = 0
    retention_deleted: int = 0

    def snapshot(self) -> dict[str, Any]:
        return {
            "obs_ingest_batch_size": self.ingest_batch_size,
            "obs_ingest_duration_ms": self.ingest_duration_ms,
            "obs_ingest_last_success_at": (
                self.ingest_last_success_at.isoformat()
                if self.ingest_last_success_at
                else None
            ),
            "obs_ingest_retry_count": self.ingest_retry_count,
            "obs_ingest_records_consumed": self.ingest_records_consumed,
            "obs_ingest_failure_count": self.ingest_failure_count,
            "obs_stream_reclaimed_records": self.reclaimed_records,
            "obs_stream_invalid_records": self.invalid_records,
            "obs_stream_dlq_records": self.dlq_records,
            "obs_stream_length": self.stream_length,
            "obs_stream_pending": self.consumer_pending,
            "obs_stream_consumer_lag": self.consumer_lag,
            "obs_retention_deleted": self.retention_deleted,
        }


class RedisObsIngestWorker:
    """Consume OBS records at least once and acknowledge after MySQL commit."""

    def __init__(
        self,
        client: Any,
        store: ObservabilityStore,
        consumer_name: str,
        *,
        stream_key: str = DEFAULT_STREAM_KEY,
        group_name: str = DEFAULT_GROUP_NAME,
        dlq_key: str = DEFAULT_DLQ_KEY,
        producers_key: str = DEFAULT_PRODUCERS_KEY,
        batch_max: int = DEFAULT_BATCH_MAX,
        block_ms: int = DEFAULT_BLOCK_MS,
        claim_idle_ms: int = DEFAULT_CLAIM_IDLE_MS,
        maintenance_interval_seconds: float = DEFAULT_MAINTENANCE_INTERVAL_SECONDS,
        producer_stale_seconds: float = DEFAULT_PRODUCER_STALE_SECONDS,
        durability_timeout_ms: int = DEFAULT_DURABILITY_TIMEOUT_MS,
        wait_replicas: int = 0,
        retention_days: int | None = None,
        raw_root: Path | None = None,
    ) -> None:
        self.client = client
        self.store = store
        self.consumer_name = consumer_name
        self.stream_key = stream_key
        self.group_name = group_name
        self.dlq_key = dlq_key
        self.producers_key = producers_key
        self.batch_max = batch_max
        self.block_ms = block_ms
        self.claim_idle_ms = claim_idle_ms
        self.maintenance_interval_seconds = maintenance_interval_seconds
        self.producer_stale_seconds = producer_stale_seconds
        self.durability_timeout_ms = durability_timeout_ms
        self.wait_replicas = wait_replicas
        self.retention_days = retention_days
        self.raw_root = raw_root
        self.metrics = RedisIngestMetrics()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._last_cleanup_at = 0.0
        self._claim_start_id = "0-0"

    @classmethod
    def from_url(
        cls,
        url: str,
        store: ObservabilityStore,
        consumer_name: str,
        *,
        socket_timeout: float = 2.0,
        **kwargs: Any,
    ) -> "RedisObsIngestWorker":
        client = redis_async.from_url(
            url,
            socket_timeout=max(socket_timeout, DEFAULT_BLOCK_MS / 1000 + 1),
            socket_connect_timeout=socket_timeout,
            decode_responses=True,
        )
        return cls(client, store, consumer_name, **kwargs)

    async def initialize(self) -> None:
        try:
            await self.client.xgroup_create(
                self.stream_key,
                self.group_name,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise ObsBufferError("OBS Redis consumer group initialization failed") from exc

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(
            self._run(), name=f"obs-redis-consumer-{self.consumer_name}"
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._stop.set()
            await self._task
            self._task = None
        await self.client.aclose()

    async def _run(self) -> None:
        next_maintenance_at = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_maintenance_at:
                await self._run_maintenance()
                next_maintenance_at = now + self.maintenance_interval_seconds
            try:
                with suppress_observation():
                    response = await self.client.xreadgroup(
                        self.group_name,
                        self.consumer_name,
                        {self.stream_key: ">"},
                        count=self.batch_max,
                        block=self.block_ms,
                    )
                entries = self._entries_from_read(response)
                if entries:
                    await self._process_entries(entries)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - pending messages remain recoverable
                self.metrics.ingest_retry_count += 1
                self.metrics.ingest_failure_count += 1
                logger.warning("OBS Redis consumer poll failed", exc_info=True)
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=1.0)
                except TimeoutError:
                    pass

    async def _run_maintenance(self) -> None:
        try:
            await self._reclaim_stale()
        except Exception:  # noqa: BLE001
            logger.warning("OBS Redis pending reclaim failed", exc_info=True)
        try:
            await self._refresh_stream_metrics()
            await self._reconcile_interrupted_producers()
        except Exception:  # noqa: BLE001
            logger.warning("OBS Redis maintenance failed", exc_info=True)
        try:
            await self._check_retention_cleanup()
        except Exception:  # noqa: BLE001
            logger.warning("OBS retention cleanup failed", exc_info=True)

    async def _reclaim_stale(self) -> None:
        with suppress_observation():
            response = await self.client.xautoclaim(
                self.stream_key,
                self.group_name,
                self.consumer_name,
                self.claim_idle_ms,
                start_id=self._claim_start_id,
                count=self.batch_max,
            )
        self._claim_start_id, entries = self._autoclaim_result(response)
        if not entries:
            return
        self.metrics.reclaimed_records += len(entries)
        await self._process_entries(entries)

    async def _process_entries(
        self, entries: list[tuple[str, dict[Any, Any]]]
    ) -> None:
        valid: list[tuple[str, ObsRecord]] = []
        for message_id, fields in entries:
            try:
                encoded = fields.get("record") or fields.get(b"record")
                if isinstance(encoded, bytes):
                    encoded = encoded.decode("utf-8")
                if not isinstance(encoded, str):
                    raise ValueError("stream entry has no record field")
                valid.append((message_id, ObsRecord.model_validate_json(encoded)))
            except (ValidationError, ValueError, UnicodeError) as exc:
                self.metrics.invalid_records += 1
                await self._dead_letter(message_id, fields, str(exc))

        if not valid:
            return
        started = time.perf_counter()
        try:
            await self.store.bulk_upsert([record for _, record in valid])
        except Exception:
            self.metrics.ingest_failure_count += 1
            logger.warning(
                "OBS Redis batch ingest failed; retrying records individually",
                exc_info=True,
            )
            await self._process_individually(valid)
            return
        self.metrics.ingest_batch_size = len(valid)
        self.metrics.ingest_duration_ms = (time.perf_counter() - started) * 1000.0
        self.metrics.ingest_last_success_at = datetime.now(timezone.utc)
        await self._ack_and_delete([message_id for message_id, _ in valid])

    async def _process_individually(
        self, entries: list[tuple[str, ObsRecord]]
    ) -> None:
        for message_id, record in entries:
            try:
                await self.store.bulk_upsert([record])
            except Exception:  # noqa: BLE001 - leave pending for XAUTOCLAIM
                self.metrics.ingest_retry_count += 1
                logger.warning(
                    "OBS Redis record %s ingest failed; left pending",
                    message_id,
                    exc_info=True,
                )
                continue
            self.metrics.ingest_last_success_at = datetime.now(timezone.utc)
            await self._ack_and_delete([message_id])

    async def _ack_and_delete(self, message_ids: list[str]) -> None:
        if not message_ids:
            return
        pipeline = self.client.pipeline(transaction=False)
        pipeline.xack(self.stream_key, self.group_name, *message_ids)
        pipeline.xdel(self.stream_key, *message_ids)
        with suppress_observation():
            await pipeline.execute()
        self.metrics.ingest_records_consumed += len(message_ids)

    async def _dead_letter(
        self,
        message_id: str,
        fields: dict[Any, Any],
        reason: str,
    ) -> None:
        encoded = fields.get("record") or fields.get(b"record") or ""
        if isinstance(encoded, bytes):
            encoded = encoded.decode("utf-8", errors="replace")
        pipeline = self.client.pipeline(transaction=False)
        pipeline.xadd(
            self.dlq_key,
            {
                "source_stream": self.stream_key,
                "source_id": message_id,
                "record": str(encoded),
                "reason": reason[:2_000],
                "failed_at": datetime.now(timezone.utc).isoformat(),
            },
            maxlen=DEFAULT_DLQ_MAXLEN,
            approximate=True,
        )
        pipeline.execute_command(
            "WAITAOF", 1, self.wait_replicas, self.durability_timeout_ms
        )
        with suppress_observation():
            responses = await pipeline.execute()
        durability = responses[-1]
        if (
            not isinstance(durability, (list, tuple))
            or len(durability) != 2
            or int(durability[0]) < 1
            or int(durability[1]) < self.wait_replicas
        ):
            raise ObsBufferError("OBS DLQ entry was not durably persisted")
        await self._ack_and_delete([message_id])
        self.metrics.dlq_records += 1

    async def _refresh_stream_metrics(self) -> None:
        with suppress_observation():
            self.metrics.stream_length = int(await self.client.xlen(self.stream_key))
            pending = await self.client.xpending(self.stream_key, self.group_name)
            groups = await self.client.xinfo_groups(self.stream_key)
        self.metrics.consumer_pending = self._pending_count(pending)
        for group in groups:
            name = group.get("name") or group.get(b"name")
            if isinstance(name, bytes):
                name = name.decode("utf-8")
            if name != self.group_name:
                continue
            lag = group.get("lag", group.get(b"lag", 0))
            self.metrics.consumer_lag = int(lag or 0)
            break

    async def _reconcile_interrupted_producers(self) -> None:
        if self.metrics.consumer_pending or self.metrics.consumer_lag:
            return
        reconcile = getattr(self.store, "fail_interrupted_producers", None)
        if reconcile is None:
            return
        cutoff = time.time() - self.producer_stale_seconds
        with suppress_observation():
            stale = await self.client.zrangebyscore(
                self.producers_key, "-inf", cutoff
            )
        producer_ids = [
            item.decode("utf-8") if isinstance(item, bytes) else str(item)
            for item in stale
        ]
        if not producer_ids:
            return
        counts = await reconcile(
            producer_ids, interrupted_at=datetime.now(timezone.utc)
        )
        with suppress_observation():
            await self.client.zrem(self.producers_key, *producer_ids)
        if sum(counts.values()):
            logger.warning(
                "Marked interrupted OBS rows failed for %d stale Redis producers: %s",
                len(producer_ids),
                counts,
            )

    async def _check_retention_cleanup(self) -> None:
        if self.retention_days is None or self.retention_days <= 0:
            return
        now = time.monotonic()
        if now - self._last_cleanup_at < 24 * 3600:
            return
        self._last_cleanup_at = now
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
        deleted = await self.store.delete_older_than(cutoff)
        self.metrics.retention_deleted += sum(deleted.values())
        await self._cleanup_raw_files(cutoff)

    async def _cleanup_raw_files(self, cutoff: datetime) -> None:
        if self.raw_root is None or not self.raw_root.is_dir():
            return
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
                    try:
                        row = await self.store.get_trace(trace_dir.name)
                    except Exception:  # noqa: BLE001 - cleanup is conservative
                        continue
                    if row is None:
                        await asyncio.to_thread(
                            shutil.rmtree, trace_dir, ignore_errors=True
                        )

    @staticmethod
    def _entries_from_read(response: Any) -> list[tuple[str, dict[Any, Any]]]:
        entries: list[tuple[str, dict[Any, Any]]] = []
        for _, messages in response or []:
            for message_id, fields in messages:
                if isinstance(message_id, bytes):
                    message_id = message_id.decode("utf-8")
                entries.append((str(message_id), fields))
        return entries

    @staticmethod
    def _autoclaim_result(
        response: Any,
    ) -> tuple[str, list[tuple[str, dict[Any, Any]]]]:
        if not response or len(response) < 2:
            return "0-0", []
        next_start_id = response[0]
        if isinstance(next_start_id, bytes):
            next_start_id = next_start_id.decode("utf-8")
        messages = response[1] or []
        return (
            str(next_start_id or "0-0"),
            RedisObsIngestWorker._entries_from_read(
                [(DEFAULT_STREAM_KEY, messages)]
            ),
        )

    @staticmethod
    def _pending_count(pending: Any) -> int:
        if isinstance(pending, dict):
            value = pending.get("pending", pending.get(b"pending", 0))
            return int(value or 0)
        if isinstance(pending, (list, tuple)) and pending:
            return int(pending[0] or 0)
        return 0
