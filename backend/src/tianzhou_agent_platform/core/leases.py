"""The shared key-value operations services use to exclude each other across nodes (``RedisStore``).

Callers that are given no ``LeaseStore`` fall back to process-local exclusion, which only holds on one node.
"""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from tianzhou_agent_platform.store.models import DeleteResult, WriteResult


class LeaseStore(Protocol):
    def lease(
        self,
        namespace: str,
        key: str,
        *,
        ttl_seconds: int,
        blocking_timeout_seconds: float | None = None,
    ) -> AbstractAsyncContextManager[bool]: ...

    async def set_if_absent(
        self,
        namespace: str,
        key: str,
        value: Any,
        ttl_seconds: int | None = None,
    ) -> WriteResult: ...

    async def delete(self, namespace: str, key: str) -> DeleteResult: ...

    async def exists(self, namespace: str, key: str) -> bool: ...
