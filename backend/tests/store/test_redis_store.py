import pytest

from tianzhou_agent_platform.store import RedisStore, StorageValidationError


class FakeRedisClient:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}
        self.locked: set[str] = set()
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

    async def acquire(self, *, blocking: bool) -> bool:
        if self.key in self.client.locked:
            return False
        self.client.locked.add(self.key)
        return True

    async def extend(self, ttl: int, *, replace_ttl: bool) -> bool:
        return self.key in self.client.locked

    async def release(self) -> None:
        self.client.locked.remove(self.key)


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
