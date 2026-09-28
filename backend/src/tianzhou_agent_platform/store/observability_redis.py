"""Redis Streams backed durable buffer for OBS records."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from redis import asyncio as redis_async
from redis.exceptions import RedisError

from tianzhou_agent_platform.core.observation_context import suppress_observation
from tianzhou_agent_platform.store.observability_buffer import (
    ObsBufferError,
    ObsBufferFlushTimeoutError,
    ObsBufferGapError,
    ObsRecord,
)

logger = logging.getLogger(__name__)

DEFAULT_STREAM_KEY = "unibot:obs:records:v1"
DEFAULT_PRODUCERS_KEY = "unibot:obs:producers:v1"
DEFAULT_QUEUE_CAPACITY = 10_000
DEFAULT_BATCH_MAX = 256
DEFAULT_FLUSH_TIMEOUT_SECONDS = 10.0
DEFAULT_DURABILITY_TIMEOUT_MS = 10_000
DEFAULT_RETRY_BACKOFF_SECONDS = 1.0
DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 10.0
MAX_RECORD_BYTES = 8 * 1024 * 1024


@dataclass(slots=True)
class RedisBufferMetrics:
    queue_capacity: int
    queue_depth: int = 0
    sequence_no: int = 0
    durable_through: int = 0
    published_records: int = 0
    publish_batch_count: int = 0
    publish_failure_count: int = 0
    durability_failure_count: int = 0
    telemetry_gap_count: int = 0
    heartbeat_failure_count: int = 0
    last_publish_duration_ms: float = 0.0

    def snapshot(self) -> dict[str, Any]:
        return {
            "obs_buffer_backend": "redis",
            "obs_queue_depth": self.queue_depth,
            "obs_queue_capacity": self.queue_capacity,
            "obs_buffer_sequence_no": self.sequence_no,
            "obs_buffer_durable_through": self.durable_through,
            "obs_stream_published_records": self.published_records,
            "obs_stream_publish_batch_count": self.publish_batch_count,
            "obs_stream_publish_failure_count": self.publish_failure_count,
            "obs_stream_durability_failure_count": self.durability_failure_count,
            "obs_telemetry_gap_count": self.telemetry_gap_count,
            "obs_stream_heartbeat_failure_count": self.heartbeat_failure_count,
            "obs_stream_publish_duration_ms": self.last_publish_duration_ms,
        }


class RedisObsBuffer:
    """Thread-safe synchronous submit facade with async Redis persistence.

    OpenTelemetry ``SpanProcessor.on_end`` is synchronous, so callers enqueue
    records without awaiting network I/O. A single async publisher preserves
    submission order. Each batch uses one non-transactional Redis pipeline:
    ``XADD ...`` followed by ``WAITAOF`` on the same connection. A durability
    barrier is released only when Redis confirms the requested AOF writes.
    """

    def __init__(
        self,
        client: Any,
        heartbeat_client: Any,
        producer_instance_id: str,
        *,
        stream_key: str = DEFAULT_STREAM_KEY,
        producers_key: str = DEFAULT_PRODUCERS_KEY,
        queue_capacity: int = DEFAULT_QUEUE_CAPACITY,
        batch_max: int = DEFAULT_BATCH_MAX,
        flush_timeout_seconds: float = DEFAULT_FLUSH_TIMEOUT_SECONDS,
        durability_timeout_ms: int = DEFAULT_DURABILITY_TIMEOUT_MS,
        wait_replicas: int = 0,
        retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_SECONDS,
        heartbeat_interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
    ) -> None:
        self.client = client
        self.heartbeat_client = heartbeat_client
        self.producer_instance_id = producer_instance_id
        self.stream_key = stream_key
        self.producers_key = producers_key
        self.batch_max = batch_max
        self.flush_timeout_seconds = flush_timeout_seconds
        self.durability_timeout_ms = durability_timeout_ms
        self.wait_replicas = wait_replicas
        self.retry_backoff_seconds = retry_backoff_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.metrics = RedisBufferMetrics(queue_capacity=queue_capacity)

        self._pending: deque[tuple[ObsRecord, str]] = deque()
        self._inflight: list[tuple[ObsRecord, str]] = []
        self._submit_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._sequence_no = 0
        self._durable_through = 0
        self._first_gap_sequence: int | None = None
        self._waiters: dict[int, list[asyncio.Future[None]]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._closing = False
        self._closed = False
        self._failure: ObsBufferError | None = None

    @classmethod
    def from_url(
        cls,
        url: str,
        producer_instance_id: str,
        *,
        socket_timeout: float = 2.0,
        **kwargs: Any,
    ) -> "RedisObsBuffer":
        client = redis_async.from_url(
            url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
            decode_responses=True,
        )
        heartbeat_client = redis_async.from_url(
            url,
            socket_timeout=socket_timeout,
            socket_connect_timeout=socket_timeout,
            decode_responses=True,
        )
        return cls(client, heartbeat_client, producer_instance_id, **kwargs)

    async def initialize(self) -> None:
        """Verify that Redis can provide the durability contract we expose."""

        try:
            await self.client.ping()
            server = await self.client.info("server")
            version_text = str(server.get("redis_version") or "0.0")
            version = tuple(int(part) for part in version_text.split(".")[:2])
            if version < (7, 2):
                raise ObsBufferError(
                    f"Redis 7.2+ is required for WAITAOF; server reports {version_text}"
                )
            persistence = await self.client.info("persistence")
            if int(persistence.get("aof_enabled") or 0) != 1:
                raise ObsBufferError(
                    "OBS Redis must enable AOF persistence (appendonly yes)"
                )
        except ObsBufferError:
            raise
        except (RedisError, OSError, ValueError) as exc:
            raise ObsBufferError("OBS Redis durability check failed") from exc

    @property
    def sequence_no(self) -> int:
        with self._state_lock:
            return self._sequence_no

    @property
    def durable_through(self) -> int:
        with self._state_lock:
            return self._durable_through

    def start(self) -> None:
        if self._task is not None:
            return
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(), name=f"obs-redis-publisher-{self.producer_instance_id}"
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"obs-redis-heartbeat-{self.producer_instance_id}"
        )

    def submit(self, record: ObsRecord) -> int:
        """Validate and enqueue one record from sync or async application code."""

        with self._submit_lock:
            with self._state_lock:
                if self._failure is not None:
                    raise self._failure
                if self._closing or self._closed or self._wake is None:
                    raise ObsBufferError("OBS Redis buffer is not running")
                sequence_no = self._sequence_no + 1
                self._sequence_no = sequence_no
                self.metrics.sequence_no = sequence_no
            record.sequence_no = sequence_no
            try:
                encoded = record.model_dump_json()
                if len(encoded.encode("utf-8")) > MAX_RECORD_BYTES:
                    raise ValueError(
                        f"OBS record exceeds {MAX_RECORD_BYTES} bytes"
                    )
            except Exception as exc:  # noqa: BLE001 - isolate a bad telemetry record
                self._record_gaps([sequence_no], "OBS record validation failed")
                raise ObsBufferError("OBS record rejected before Redis enqueue") from exc

            with self._state_lock:
                if len(self._pending) >= self.metrics.queue_capacity:
                    self._record_gaps_locked([sequence_no])
                    raise ObsBufferGapError("OBS Redis buffer queue is full")
                self._pending.append((record, encoded))
                self.metrics.queue_depth = len(self._pending)
                wake = self._wake
            if wake is not None and self._loop is not None:
                self._loop.call_soon_threadsafe(wake.set)
            return sequence_no

    async def flush_through(self, sequence_no: int) -> None:
        """Wait until every accepted record through ``sequence_no`` is durable."""

        loop = asyncio.get_running_loop()
        future: asyncio.Future[None] = loop.create_future()
        with self._state_lock:
            if (
                self._first_gap_sequence is not None
                and self._first_gap_sequence <= sequence_no
            ):
                raise ObsBufferGapError(
                    f"Redis durability barrier {sequence_no} covers a telemetry gap"
                )
            if sequence_no <= self._durable_through:
                return
            if self._failure is not None:
                raise self._failure
            self._waiters.setdefault(sequence_no, []).append(future)
        try:
            await asyncio.wait_for(future, timeout=self.flush_timeout_seconds)
        except TimeoutError as exc:
            with self._state_lock:
                waiters = self._waiters.get(sequence_no)
                if waiters is not None and future in waiters:
                    waiters.remove(future)
                    if not waiters:
                        self._waiters.pop(sequence_no, None)
            raise ObsBufferFlushTimeoutError(
                f"Redis durability barrier {sequence_no} timed out after "
                f"{self.flush_timeout_seconds}s"
            ) from exc

    def close(self) -> None:
        with self._state_lock:
            if self._closing or self._closed:
                return
            self._closing = True
            wake = self._wake
        if wake is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(wake.set)

    async def wait_closed(self) -> None:
        if self._task is not None:
            try:
                await asyncio.wait_for(
                    asyncio.shield(self._task), timeout=self.flush_timeout_seconds
                )
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
                self._mark_failed(ObsBufferError("OBS Redis publisher shutdown timed out"))
            self._task = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None
        self._closed = True
        await self.client.aclose()
        await self.heartbeat_client.aclose()

    async def _run(self) -> None:
        assert self._wake is not None
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                while True:
                    batch = self._take_batch()
                    if not batch:
                        with self._state_lock:
                            closing = self._closing
                        if closing:
                            return
                        break
                    while True:
                        try:
                            await self._publish_batch(batch)
                        except asyncio.CancelledError:
                            raise
                        except Exception:  # noqa: BLE001 - retry preserves the batch in memory
                            self.metrics.publish_failure_count += 1
                            logger.warning(
                                "OBS Redis publish failed; retrying %d records in %.2fs",
                                len(batch),
                                self.retry_backoff_seconds,
                                exc_info=True,
                            )
                            await asyncio.sleep(self.retry_backoff_seconds)
                            continue
                        self._notify_durable(batch[-1][0].sequence_no)
                        with self._state_lock:
                            self._inflight = []
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("OBS Redis publisher stopped")
            self._mark_failed(ObsBufferError("OBS Redis publisher stopped"))
            self._failure.__cause__ = exc  # type: ignore[union-attr]
        finally:
            with self._state_lock:
                unfinished = [
                    record.sequence_no
                    for record, _ in [*self._inflight, *self._pending]
                ]
            if unfinished:
                self._record_gaps(unfinished, "OBS Redis publisher stopped with pending records")

    def _take_batch(self) -> list[tuple[ObsRecord, str]]:
        with self._state_lock:
            batch: list[tuple[ObsRecord, str]] = []
            while self._pending and len(batch) < self.batch_max:
                batch.append(self._pending.popleft())
            self._inflight = list(batch)
            self.metrics.queue_depth = len(self._pending)
            return batch

    async def _publish_batch(self, batch: list[tuple[ObsRecord, str]]) -> None:
        started = time.perf_counter()
        pipeline = self.client.pipeline(transaction=False)
        for _, encoded in batch:
            pipeline.xadd(self.stream_key, {"record": encoded})
        pipeline.execute_command(
            "WAITAOF",
            1,
            self.wait_replicas,
            self.durability_timeout_ms,
        )
        with suppress_observation():
            responses = await pipeline.execute()
        durability = responses[-1]
        if not isinstance(durability, (list, tuple)) or len(durability) != 2:
            self.metrics.durability_failure_count += 1
            raise ObsBufferError("Redis returned an invalid WAITAOF response")
        local, replicas = int(durability[0]), int(durability[1])
        if local < 1 or replicas < self.wait_replicas:
            self.metrics.durability_failure_count += 1
            raise ObsBufferError(
                f"Redis WAITAOF durability target not met: local={local}, replicas={replicas}"
            )
        self.metrics.last_publish_duration_ms = (
            time.perf_counter() - started
        ) * 1000.0
        self.metrics.publish_batch_count += 1
        self.metrics.published_records += len(batch)

    async def _heartbeat_loop(self) -> None:
        while True:
            try:
                with suppress_observation():
                    await self.heartbeat_client.zadd(
                        self.producers_key,
                        {self.producer_instance_id: time.time()},
                    )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - heartbeat failure is observable, not fatal
                self.metrics.heartbeat_failure_count += 1
                logger.warning("OBS Redis producer heartbeat failed", exc_info=True)
            await asyncio.sleep(self.heartbeat_interval_seconds)

    def _notify_durable(self, sequence_no: int) -> None:
        succeeded: list[asyncio.Future[None]] = []
        failed: list[asyncio.Future[None]] = []
        with self._state_lock:
            self._durable_through = max(self._durable_through, sequence_no)
            self.metrics.durable_through = self._durable_through
            resolved = [key for key in self._waiters if key <= self._durable_through]
            for key in resolved:
                futures = self._waiters.pop(key)
                if (
                    self._first_gap_sequence is not None
                    and self._first_gap_sequence <= key
                ):
                    failed.extend(futures)
                else:
                    succeeded.extend(futures)
        for future in succeeded:
            self._set_future_result(future)
        error = ObsBufferGapError("Redis durability barrier covers a telemetry gap")
        for future in failed:
            self._set_future_exception(future, error)

    def _record_gaps(self, sequence_nos: list[int], message: str) -> None:
        with self._state_lock:
            failed = self._record_gaps_locked(sequence_nos)
        error = ObsBufferGapError(message)
        for future in failed:
            self._set_future_exception(future, error)

    def _record_gaps_locked(
        self, sequence_nos: list[int]
    ) -> list[asyncio.Future[None]]:
        first_gap = min(sequence_nos)
        if self._first_gap_sequence is None:
            self._first_gap_sequence = first_gap
        else:
            self._first_gap_sequence = min(self._first_gap_sequence, first_gap)
        self.metrics.telemetry_gap_count += len(sequence_nos)
        failed: list[asyncio.Future[None]] = []
        affected = [
            key
            for key in self._waiters
            if self._first_gap_sequence <= key
        ]
        for key in affected:
            failed.extend(self._waiters.pop(key))
        return failed

    def _mark_failed(self, error: ObsBufferError) -> None:
        waiters: list[asyncio.Future[None]] = []
        with self._state_lock:
            if self._failure is None:
                self._failure = error
            self._closing = True
            for futures in self._waiters.values():
                waiters.extend(futures)
            self._waiters.clear()
        for future in waiters:
            self._set_future_exception(future, error)

    @staticmethod
    def _set_future_result(future: asyncio.Future[None]) -> None:
        future.get_loop().call_soon_threadsafe(
            RedisObsBuffer._set_result_if_pending, future
        )

    @staticmethod
    def _set_future_exception(
        future: asyncio.Future[None], error: Exception
    ) -> None:
        future.get_loop().call_soon_threadsafe(
            RedisObsBuffer._set_exception_if_pending, future, error
        )

    @staticmethod
    def _set_result_if_pending(future: asyncio.Future[None]) -> None:
        if not future.done():
            future.set_result(None)

    @staticmethod
    def _set_exception_if_pending(
        future: asyncio.Future[None], error: Exception
    ) -> None:
        if not future.done():
            future.set_exception(error)
