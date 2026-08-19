from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from tianzhou_agent_platform.core.observability_stream import RedisObsIngestWorker
from tianzhou_agent_platform.store.observability_buffer import ObsBufferError, ObsRecord
from tianzhou_agent_platform.store.observability_redis import RedisObsBuffer


def make_record(record_id: str = "obsrec_1") -> ObsRecord:
    return ObsRecord(
        record_id=record_id,
        record_type="event",
        producer_instance_id="node-1",
        sequence_no=0,
        occurred_at=datetime.now(timezone.utc),
        trace_id="a" * 32,
        payload={"name": "test.event", "status": "completed"},
    )


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.commands: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def xadd(self, key: str, fields: dict[str, Any], **kwargs: Any) -> "FakePipeline":
        self.commands.append(("xadd", (key, fields), kwargs))
        return self

    def execute_command(self, *args: Any) -> "FakePipeline":
        self.commands.append(("execute_command", args, {}))
        return self

    def xack(self, *args: Any) -> "FakePipeline":
        self.commands.append(("xack", args, {}))
        return self

    def xdel(self, *args: Any) -> "FakePipeline":
        self.commands.append(("xdel", args, {}))
        return self

    async def execute(self) -> list[Any]:
        if self.redis.fail_pipeline_count:
            self.redis.fail_pipeline_count -= 1
            raise ConnectionError("redis unavailable")
        self.redis.executed_pipelines.append(list(self.commands))
        responses: list[Any] = []
        for command, args, _ in self.commands:
            if command == "xadd":
                key, fields = args
                self.redis.next_id += 1
                message_id = f"{self.redis.next_id}-0"
                self.redis.streams.setdefault(str(key), []).append((message_id, fields))
                responses.append(message_id)
            elif command == "execute_command":
                assert args[0] == "WAITAOF"
                responses.append(list(self.redis.waitaof_response))
            elif command == "xack":
                self.redis.acked.extend(str(item) for item in args[2:])
                responses.append(len(args) - 2)
            elif command == "xdel":
                self.redis.deleted.extend(str(item) for item in args[1:])
                responses.append(len(args) - 1)
        return responses


class FakeRedis:
    def __init__(self, *, aof_enabled: int = 1) -> None:
        self.aof_enabled = aof_enabled
        self.waitaof_response = (1, 0)
        self.fail_pipeline_count = 0
        self.next_id = 0
        self.streams: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        self.executed_pipelines: list[list[tuple[str, tuple[Any, ...], dict[str, Any]]]] = []
        self.heartbeats: list[tuple[str, dict[str, float]]] = []
        self.acked: list[str] = []
        self.deleted: list[str] = []
        self.closed = False

    async def ping(self) -> bool:
        return True

    async def info(self, section: str) -> dict[str, Any]:
        if section == "server":
            return {"redis_version": "7.2.9"}
        return {"aof_enabled": self.aof_enabled}

    def pipeline(self, *, transaction: bool) -> FakePipeline:
        assert transaction is False
        return FakePipeline(self)

    async def zadd(self, key: str, values: dict[str, float]) -> int:
        self.heartbeats.append((key, values))
        return 1

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_redis_buffer_publishes_and_releases_durability_barrier() -> None:
    redis = FakeRedis()
    heartbeat = FakeRedis()
    buffer = RedisObsBuffer(
        redis,
        heartbeat,
        "node-1",
        retry_backoff_seconds=0.01,
        heartbeat_interval_seconds=60,
    )

    await buffer.initialize()
    buffer.start()
    first = buffer.submit(make_record("obsrec_1"))
    second = buffer.submit(make_record("obsrec_2"))
    await buffer.flush_through(second)

    assert first == 1
    assert second == 2
    assert buffer.durable_through == 2
    assert len(redis.streams[buffer.stream_key]) == 2
    assert redis.executed_pipelines[0][-1][1][0] == "WAITAOF"
    assert buffer.metrics.published_records == 2

    buffer.close()
    await buffer.wait_closed()
    assert redis.closed is True
    assert heartbeat.closed is True


@pytest.mark.asyncio
async def test_redis_buffer_retries_without_releasing_barrier_early() -> None:
    redis = FakeRedis()
    redis.fail_pipeline_count = 1
    buffer = RedisObsBuffer(
        redis,
        FakeRedis(),
        "node-1",
        retry_backoff_seconds=0.01,
        heartbeat_interval_seconds=60,
    )
    await buffer.initialize()
    buffer.start()

    sequence = buffer.submit(make_record())
    await buffer.flush_through(sequence)

    assert buffer.metrics.publish_failure_count == 1
    assert buffer.durable_through == sequence
    buffer.close()
    await buffer.wait_closed()


@pytest.mark.asyncio
async def test_redis_buffer_rejects_non_durable_redis() -> None:
    buffer = RedisObsBuffer(FakeRedis(aof_enabled=0), FakeRedis(), "node-1")

    with pytest.raises(ObsBufferError, match="appendonly yes"):
        await buffer.initialize()


class FakeStore:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.batches: list[list[ObsRecord]] = []

    async def bulk_upsert(self, records: list[ObsRecord]) -> int:
        if self.fail:
            raise RuntimeError("mysql unavailable")
        self.batches.append(records)
        return len(records)


@pytest.mark.asyncio
async def test_consumer_acknowledges_only_after_mysql_commit() -> None:
    redis = FakeRedis()
    store = FakeStore()
    worker = RedisObsIngestWorker(redis, store, "consumer-1")  # type: ignore[arg-type]
    encoded = make_record().model_dump_json()

    await worker._process_entries([("1-0", {"record": encoded})])  # noqa: SLF001

    assert len(store.batches) == 1
    assert redis.acked == ["1-0"]
    assert redis.deleted == ["1-0"]
    assert worker.metrics.ingest_records_consumed == 1


@pytest.mark.asyncio
async def test_consumer_leaves_message_pending_when_mysql_fails() -> None:
    redis = FakeRedis()
    worker = RedisObsIngestWorker(redis, FakeStore(fail=True), "consumer-1")  # type: ignore[arg-type]

    await worker._process_entries(  # noqa: SLF001
        [("1-0", {"record": make_record().model_dump_json()})]
    )

    assert redis.acked == []
    assert redis.deleted == []
    assert worker.metrics.ingest_retry_count == 1


@pytest.mark.asyncio
async def test_invalid_record_is_durably_dead_lettered_before_ack() -> None:
    redis = FakeRedis()
    worker = RedisObsIngestWorker(redis, FakeStore(), "consumer-1")  # type: ignore[arg-type]

    await worker._process_entries([("1-0", {"record": "not-json"})])  # noqa: SLF001

    dlq = redis.streams[worker.dlq_key]
    assert len(dlq) == 1
    assert dlq[0][1]["source_id"] == "1-0"
    assert redis.acked == ["1-0"]
    assert redis.deleted == ["1-0"]
    assert worker.metrics.dlq_records == 1


def test_autoclaim_cursor_advances_across_large_pending_sets() -> None:
    next_id, entries = RedisObsIngestWorker._autoclaim_result(  # noqa: SLF001
        [b"42-0", [(b"7-0", {b"record": b"{}"})], []]
    )

    assert next_id == "42-0"
    assert entries == [("7-0", {b"record": b"{}"})]


@pytest.mark.asyncio
async def test_flush_waiter_is_cancel_safe() -> None:
    redis = FakeRedis()
    redis.fail_pipeline_count = 100
    buffer = RedisObsBuffer(
        redis,
        FakeRedis(),
        "node-1",
        retry_backoff_seconds=0.01,
        flush_timeout_seconds=0.02,
        heartbeat_interval_seconds=60,
    )
    await buffer.initialize()
    buffer.start()
    sequence = buffer.submit(make_record())

    with pytest.raises(ObsBufferError):
        await buffer.flush_through(sequence)

    buffer.close()
    await buffer.wait_closed()
    await asyncio.sleep(0)
