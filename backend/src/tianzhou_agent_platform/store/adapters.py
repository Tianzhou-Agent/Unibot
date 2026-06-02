"""Storage adapter protocol."""

from __future__ import annotations

from typing import Protocol

from .models import StorageAck, StorageObject, StoragePage, StorageWrite


class StorageAdapter(Protocol):
    name: str
    supports_ttl: bool
    supports_ordered_list: bool

    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...

    async def get(self, namespace: str, key: str) -> StorageObject | None: ...

    async def exists(self, namespace: str, key: str) -> bool: ...

    async def create(self, namespace: str, key: str, resource: StorageWrite) -> StorageAck: ...

    async def put(self, namespace: str, key: str, resource: StorageWrite) -> StorageAck: ...

    async def delete(self, namespace: str, key: str) -> bool: ...

    async def list(
        self,
        namespace: str,
        prefix: str | None,
        page_size: int,
        page_token: str | None,
    ) -> StoragePage: ...
