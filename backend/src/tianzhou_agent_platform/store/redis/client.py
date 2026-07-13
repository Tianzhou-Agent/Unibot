from __future__ import annotations

import inspect
import json
from typing import Any

from redis import asyncio as redis_async
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import RedisError, TimeoutError as RedisTimeoutError

from tianzhou_agent_platform.store.errors import (
    StorageBackendUnavailableError,
    StorageError,
    StorageTimeoutError,
    StorageUnknownBackendError,
    StorageValidationError,
)
from tianzhou_agent_platform.store.models import CacheEntry, DeleteResult, WriteResult


class RedisStore:
    def __init__(self, client: redis_async.Redis, default_ttl_seconds: int | None = None) -> None:
        self._client = client
        self._default_ttl_seconds = default_ttl_seconds

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        socket_timeout: float = 2.0,
        default_ttl_seconds: int | None = None,
    ) -> "RedisStore":
        client = redis_async.from_url(url, socket_timeout=socket_timeout)
        return cls(client, default_ttl_seconds=default_ttl_seconds)

    async def get(self, namespace: str, key: str) -> CacheEntry | None:
        redis_key = self._redis_key(namespace, key)
        try:
            raw_value = await self._client.get(redis_key)
        except RedisError as exc:
            raise self._map_redis_error(exc) from exc
        if raw_value is None:
            return None
        return CacheEntry(namespace=namespace, key=key, value=self._decode(raw_value))

    async def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        ttl_seconds: int | None = None,
    ) -> WriteResult:
        redis_key = self._redis_key(namespace, key)
        ttl = self._ttl(ttl_seconds)
        try:
            written = await self._client.set(redis_key, self._encode(value), ex=ttl)
        except RedisError as exc:
            raise self._map_redis_error(exc) from exc
        return WriteResult(written=bool(written))

    async def set_if_absent(
        self,
        namespace: str,
        key: str,
        value: Any,
        ttl_seconds: int | None = None,
    ) -> WriteResult:
        redis_key = self._redis_key(namespace, key)
        ttl = self._ttl(ttl_seconds)
        try:
            written = await self._client.set(redis_key, self._encode(value), ex=ttl, nx=True)
        except RedisError as exc:
            raise self._map_redis_error(exc) from exc
        return WriteResult(written=bool(written))

    async def delete(self, namespace: str, key: str) -> DeleteResult:
        redis_key = self._redis_key(namespace, key)
        try:
            deleted = await self._client.delete(redis_key)
        except RedisError as exc:
            raise self._map_redis_error(exc) from exc
        return DeleteResult(deleted=deleted > 0)

    async def exists(self, namespace: str, key: str) -> bool:
        redis_key = self._redis_key(namespace, key)
        try:
            exists = await self._client.exists(redis_key)
        except RedisError as exc:
            raise self._map_redis_error(exc) from exc
        return exists > 0

    async def expire(self, namespace: str, key: str, ttl_seconds: int) -> WriteResult:
        redis_key = self._redis_key(namespace, key)
        ttl = self._ttl(ttl_seconds)
        if ttl is None:
            raise StorageValidationError("Redis TTL must be provided")
        try:
            written = await self._client.expire(redis_key, ttl)
        except RedisError as exc:
            raise self._map_redis_error(exc) from exc
        return WriteResult(written=bool(written))

    async def close(self) -> None:
        close = getattr(self._client, "aclose", None) or getattr(self._client, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result

    def _redis_key(self, namespace: str, key: str) -> str:
        namespace = namespace.strip()
        key = key.strip()
        if not namespace:
            raise StorageValidationError("Redis namespace must not be empty")
        if not key:
            raise StorageValidationError("Redis key must not be empty")
        return f"{namespace}:{key}"

    def _ttl(self, ttl_seconds: int | None) -> int | None:
        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        if ttl is not None and ttl <= 0:
            raise StorageValidationError("Redis TTL must be greater than zero")
        return ttl

    def _encode(self, value: Any) -> str:
        return json.dumps(value, default=str, separators=(",", ":"))

    def _decode(self, value: bytes | str) -> Any:
        text = value.decode("utf-8") if isinstance(value, bytes) else value
        return json.loads(text)

    def _map_redis_error(self, exc: RedisError) -> StorageError:
        if isinstance(exc, RedisTimeoutError):
            return StorageTimeoutError("Redis operation timed out")
        if isinstance(exc, RedisConnectionError):
            return StorageBackendUnavailableError("Redis backend is unavailable")
        return StorageUnknownBackendError("Redis operation failed")
