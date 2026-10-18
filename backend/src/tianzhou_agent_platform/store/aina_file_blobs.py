"""The bytes of AINA installations' private files (see ``aina/package/files.py``)."""

from __future__ import annotations

from tianzhou_agent_platform.store.errors import StorageNotFoundError
from tianzhou_agent_platform.store.models import StoragePath
from tianzhou_agent_platform.store.nas.filesystem import NasStore


class NasFileBlobs:
    def __init__(self, nas: NasStore) -> None:
        self._nas = nas

    async def write(self, key: str, payload: bytes) -> None:
        await self._nas.write(StoragePath(relative_path=key), payload, overwrite=True)

    async def read(self, key: str) -> bytes:
        return await self._nas.read(StoragePath(relative_path=key))

    async def delete(self, key: str) -> None:
        try:
            await self._nas.delete(StoragePath(relative_path=key))
        except StorageNotFoundError:
            pass


class MemoryFileBlobs:
    def __init__(self) -> None:
        self._blobs: dict[str, bytes] = {}

    async def write(self, key: str, payload: bytes) -> None:
        self._blobs[key] = payload

    async def read(self, key: str) -> bytes:
        try:
            return self._blobs[key]
        except KeyError as exc:
            raise StorageNotFoundError(f"No blob {key}") from exc

    async def delete(self, key: str) -> None:
        self._blobs.pop(key, None)
