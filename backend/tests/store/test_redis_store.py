import asyncio
import os
from uuid import uuid4

import pytest
from redis.exceptions import TimeoutError as RedisTimeoutError

from tianzhou_agent_platform.store import RedisStore, StorageValidationError


class FakeRedisClient:
    def __init__(self, *, socket_timeout: float | None = None) -> None:
        # Like redis-py, a blocking pub/sub read (listen()) is bounded by socket_timeout.
        self.socket_timeout = socket_timeout
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.locked: set[str] = set()
        self.subscribers: dict[str, set["FakeRedisPubSub"]] = {}
        self.closed = False

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def eval(self, script: str, key_count: int, key: str, value: str, ttl: str = "") -> int:
        assert key_count == 1
        current = self.values.get(key)
        if "== ARGV[1]" in script:
            if current != value:
                return 0
            if "'EXPIRE'" in script:
                return int(await self.expire(key, int(ttl)))
            return await self.delete(key)
        if current is not None and int(current) >= int(value):
            return 0
        self.values[key] = value
        if ttl:
            self.expirations[key] = int(ttl)
        return 1

    async def publish(self, channel: str, value: str) -> int:
        subscribers = list(self.subscribers.get(channel, set()))
        for subscriber in subscribers:
            subscriber.queue.put_nowait({"type": "message", "data": value})
        return len(subscribers)

    def pubsub(self) -> "FakeRedisPubSub":
        return FakeRedisPubSub(self)

    async def delete(self, key: str) -> int:
        existed = key in self.values
        self.values.pop(key, None)
        return int(existed)

    async def exists(self, key: str) -> int:
        return int(key in self.values)

    async def expire(self, key: str, ttl: int) -> bool:
        if key not in self.values:
            return False
        self.expirations[key] = ttl
        return True

    async def aclose(self) -> None:
        self.closed = True

    def lock(self, key: str, *, timeout: int, blocking: bool) -> "FakeRedisLock":
        return FakeRedisLock(self, key, timeout=timeout, blocking=blocking)


class FakeRedisLock:
    def __init__(self, client: FakeRedisClient, key: str, *, timeout: int, blocking: bool) -> None:
        self.client = client
        self.key = key
        self.timeout = timeout
        self.blocking = blocking

    async def acquire(self, *, blocking: bool, blocking_timeout: float | None = None) -> bool:
        if blocking and blocking_timeout is not None:
            try:
                async with asyncio.timeout(blocking_timeout):
                    while self.key in self.client.locked:
                        await asyncio.sleep(0.01)
            except TimeoutError:
                return False
        if self.key in self.client.locked:
            return False
        self.client.locked.add(self.key)
        return True

    async def extend(self, ttl: int, *, replace_ttl: bool) -> bool:
        return self.key in self.client.locked

    async def release(self) -> None:
        self.client.locked.remove(self.key)


class FakeRedisPubSub:
    def __init__(self, client: FakeRedisClient) -> None:
        self.client = client
        self.channels: set[str] = set()
        self.queue: asyncio.Queue[dict[str, str]] = asyncio.Queue()

    async def subscribe(self, channel: str) -> None:
        self.channels.add(channel)
        self.client.subscribers.setdefault(channel, set()).add(self)

    async def unsubscribe(self, channel: str) -> None:
        self.channels.discard(channel)
        subscribers = self.client.subscribers.get(channel)
        if subscribers is not None:
            subscribers.discard(self)

    async def listen(self):  # type: ignore[no-untyped-def]
        while True:
            message = await self.get_message(timeout=None)
            if message is not None:
                yield message

    async def get_message(
        self, ignore_subscribe_messages: bool = False, timeout: float | None = 0.0
    ) -> dict[str, str] | None:
        """``timeout`` replaces socket_timeout, and returns None when no message comes; None blocks like listen()."""
        try:
            return await asyncio.wait_for(self.queue.get(), timeout if timeout is not None else self.client.socket_timeout)
        except TimeoutError:
            if timeout is None:
                raise RedisTimeoutError("Timeout reading from socket") from None
            return None

    async def aclose(self) -> None:
        for channel in list(self.channels):
            await self.unsubscribe(channel)


@pytest.mark.asyncio
async def test_redis_store_crud() -> None:
    client = FakeRedisClient()
    store = RedisStore(client)

    assert await store.get("session", "abc") is None
    assert (await store.set("session", "abc", {"user": "u1"}, ttl_seconds=30)).written is True

    entry = await store.get("session", "abc")
    assert entry is not None
    assert entry.value == {"user": "u1"}
    assert client.expirations["session:abc"] == 30
    assert await store.exists("session", "abc") is True
    assert (await store.delete("session", "abc")).deleted is True
    assert await store.exists("session", "abc") is False


@pytest.mark.asyncio
async def test_redis_store_uses_default_ttl() -> None:
    client = FakeRedisClient()
    store = RedisStore(client, default_ttl_seconds=10)

    await store.set("cache", "key", "value")

    assert client.expirations["cache:key"] == 10


@pytest.mark.asyncio
async def test_redis_store_set_if_absent_is_atomic() -> None:
    store = RedisStore(FakeRedisClient())

    first = await store.set_if_absent("lock", "conversation", {"trace_id": "trace_1"}, ttl_seconds=30)
    second = await store.set_if_absent("lock", "conversation", {"trace_id": "trace_2"}, ttl_seconds=30)

    assert first.written is True
    assert second.written is False


@pytest.mark.asyncio
async def test_redis_store_set_max_int_never_regresses() -> None:
    store = RedisStore(FakeRedisClient())

    assert (await store.set_max_int("revision", "session", 2)).written is True
    assert (await store.set_max_int("revision", "session", 1)).written is False
    assert (await store.set_max_int("revision", "session", 3)).written is True

    entry = await store.get("revision", "session")
    assert entry is not None
    assert entry.value == 3


@pytest.mark.asyncio
async def test_redis_run_lease_refresh_and_release_require_current_owner() -> None:
    client = FakeRedisClient()
    store = RedisStore(client)
    old, new = {"trace_id": "old"}, {"trace_id": "new"}
    await store.set("run", "conversation", new, ttl_seconds=30)
    assert not (await store.refresh_if_value("run", "conversation", old, ttl_seconds=90)).written
    assert client.expirations["run:conversation"] == 30
    assert not (await store.delete_if_value("run", "conversation", old)).deleted
    assert (await store.refresh_if_value("run", "conversation", new, ttl_seconds=90)).written
    assert client.expirations["run:conversation"] == 90
    assert (await store.delete_if_value("run", "conversation", new)).deleted
    assert not (await store.refresh_if_value("run", "conversation", new, ttl_seconds=90)).written


@pytest.mark.asyncio
async def test_an_idle_subscription_outlives_the_socket_timeout() -> None:
    # redis-py bounds a blocking pub/sub read by socket_timeout; an idle subscription must not end with it.
    store = RedisStore(FakeRedisClient(socket_timeout=0.05))

    async with store.subscribe("events", "session") as messages:
        waiting = asyncio.create_task(anext(messages))
        await asyncio.sleep(0.2)
        assert not waiting.done()
        assert await store.publish("events", "session", 7) == 1
        assert await asyncio.wait_for(waiting, 2) == 7


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.getenv("TZ_STORAGE_E2E") != "1",
    reason="Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml to run live storage E2E tests",
)
async def test_an_idle_subscription_outlives_the_socket_timeout_on_live_redis() -> None:
    store = RedisStore.from_url(
        os.getenv("TZ_STORAGE_E2E_REDIS_URL", "redis://127.0.0.1:16379/0"), socket_timeout=0.3
    )
    channel = f"idle-{uuid4().hex}"
    try:
        async with store.subscribe("events", channel) as messages:
            waiting = asyncio.create_task(anext(messages))
            await asyncio.sleep(1.5)
            assert not waiting.done()
            assert await store.publish("events", channel, {"n": 1}) == 1
            assert await asyncio.wait_for(waiting, 3) == {"n": 1}
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_redis_store_publish_subscribe_round_trip() -> None:
    store = RedisStore(FakeRedisClient())

    async with store.subscribe("events", "session") as messages:
        assert await store.publish("events", "session", 7) == 1
        assert await anext(messages) == 7


@pytest.mark.asyncio
async def test_redis_store_lease_has_one_owner_and_releases_safely() -> None:
    client = FakeRedisClient()
    store = RedisStore(client)

    async with store.lease("schedule", "task-1", ttl_seconds=30) as first:
        async with store.lease("schedule", "task-1", ttl_seconds=30) as second:
            assert first is True
            assert second is False
        assert "schedule:task-1" in client.locked

    assert "schedule:task-1" not in client.locked


@pytest.mark.asyncio
async def test_redis_store_state_lock_can_wait_for_a_short_read_to_finish():
    store = RedisStore(FakeRedisClient())

    async def wait_for_lock():
        async with store.lease("state", "conversation", ttl_seconds=30, blocking_timeout_seconds=1) as locked:
            assert locked

    async with store.lease("state", "conversation", ttl_seconds=30):
        waiter = asyncio.create_task(wait_for_lock())
        await asyncio.sleep(0.02)
        assert not waiter.done()
    await waiter


@pytest.mark.asyncio
async def test_redis_store_validates_key_parts_and_ttl() -> None:
    store = RedisStore(FakeRedisClient())

    with pytest.raises(StorageValidationError):
        await store.set("", "key", "value")
    with pytest.raises(StorageValidationError):
        await store.set("cache", "", "value")
    with pytest.raises(StorageValidationError):
        await store.expire("cache", "key", 0)


@pytest.mark.asyncio
async def test_redis_store_close_delegates_to_client() -> None:
    client = FakeRedisClient()
    store = RedisStore(client)

    await store.close()

    assert client.closed is True
