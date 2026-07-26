from __future__ import annotations

import asyncio
import mimetypes
import os
from datetime import datetime, timezone
from pathlib import Path

from tianzhou_agent_platform.store.errors import (
    StorageBackendUnavailableError,
    StorageError,
    StorageNotFoundError,
    StoragePolicyViolationError,
    StorageUnknownBackendError,
    StorageValidationError,
)
from tianzhou_agent_platform.store.models import DeleteResult, FileMetadata, StoragePath


class NasStore:
    def __init__(self, root_path: Path, max_file_size_bytes: int = 100 * 1024 * 1024) -> None:
        self._root_path = root_path.resolve(strict=False)
        self._max_file_size_bytes = max_file_size_bytes

    async def write(self, path: StoragePath, content: bytes, overwrite: bool = True) -> FileMetadata:
        if len(content) > self._max_file_size_bytes:
            raise StoragePolicyViolationError("NAS file content exceeds the configured size limit")
        target = self._resolve(path)
        if target.exists() and not overwrite:
            raise StorageValidationError("NAS file already exists and overwrite is disabled")
        try:
            await asyncio.to_thread(target.parent.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(self._write_bytes, target, content)
            return await self.metadata(path)
        except StorageError:
            raise
        except FileNotFoundError as exc:
            raise StorageBackendUnavailableError("NAS root path is unavailable") from exc
        except OSError as exc:
            raise StorageUnknownBackendError("NAS write operation failed") from exc

    async def read(self, path: StoragePath) -> bytes:
        target = self._resolve(path)
        try:
            return await asyncio.to_thread(self._read_bytes, target)
        except FileNotFoundError as exc:
            raise StorageNotFoundError("NAS file was not found") from exc
        except OSError as exc:
            raise StorageUnknownBackendError("NAS read operation failed") from exc

    async def delete(self, path: StoragePath) -> DeleteResult:
        target = self._resolve(path)
        try:
            await asyncio.to_thread(target.unlink)
            return DeleteResult(deleted=True)
        except FileNotFoundError:
            return DeleteResult(deleted=False)
        except OSError as exc:
            raise StorageUnknownBackendError("NAS delete operation failed") from exc

    async def create_directory(self, path: StoragePath) -> None:
        target = self._resolve(path)
        try:
            await asyncio.to_thread(target.mkdir, parents=True, exist_ok=False)
        except FileExistsError as exc:
            raise StorageValidationError("NAS directory already exists") from exc
        except FileNotFoundError as exc:
            raise StorageValidationError("NAS parent directory does not exist") from exc
        except OSError as exc:
            raise StorageUnknownBackendError("NAS directory creation failed") from exc

    async def list_directories(self, prefix: StoragePath) -> list[StoragePath]:
        target = self._resolve(prefix)
        try:
            return await asyncio.to_thread(self._list_directories, target)
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise StorageUnknownBackendError("NAS directory list operation failed") from exc

    async def move(self, source: StoragePath, destination: StoragePath) -> None:
        source_target = self._resolve(source)
        destination_target = self._resolve(destination)
        try:
            if not destination_target.parent.is_dir():
                raise StorageValidationError("NAS destination parent directory does not exist")
            await asyncio.to_thread(self._move_without_overwrite, source_target, destination_target)
        except FileExistsError as exc:
            raise StorageValidationError("NAS destination already exists") from exc
        except FileNotFoundError as exc:
            raise StorageNotFoundError("NAS source path was not found") from exc
        except StorageError:
            raise
        except OSError as exc:
            raise StorageUnknownBackendError("NAS move operation failed") from exc

    async def delete_directory(self, path: StoragePath) -> DeleteResult:
        target = self._resolve(path)
        try:
            await asyncio.to_thread(target.rmdir)
            return DeleteResult(deleted=True)
        except FileNotFoundError:
            return DeleteResult(deleted=False)
        except OSError as exc:
            if target.exists() and target.is_dir() and any(target.iterdir()):
                raise StorageValidationError("NAS directory is not empty") from exc
            raise StorageUnknownBackendError("NAS directory delete operation failed") from exc

    async def exists(self, path: StoragePath) -> bool:
        target = self._resolve(path)
        return await asyncio.to_thread(target.exists)

    async def metadata(self, path: StoragePath) -> FileMetadata:
        target = self._resolve(path)
        try:
            stat = await asyncio.to_thread(target.stat)
        except FileNotFoundError as exc:
            raise StorageNotFoundError("NAS file was not found") from exc
        except OSError as exc:
            raise StorageUnknownBackendError("NAS metadata operation failed") from exc

        content_type, _ = mimetypes.guess_type(target.name)
        modified_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
        return FileMetadata(path=path, size_bytes=stat.st_size, modified_at=modified_at, content_type=content_type)

    async def list_files(self, prefix: StoragePath) -> list[FileMetadata]:
        target = self._resolve(prefix)
        try:
            return await asyncio.to_thread(self._list_metadata, target)
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise StorageUnknownBackendError("NAS list operation failed") from exc

    def _resolve(self, path: StoragePath) -> Path:
        if not self._root_path.exists():
            raise StorageBackendUnavailableError("NAS root path is unavailable")
        target = (self._root_path / path.relative_path).resolve(strict=False)
        if not target.is_relative_to(self._root_path):
            raise StoragePolicyViolationError("NAS path escapes the configured root")
        return target

    @staticmethod
    def _write_bytes(target: Path, content: bytes) -> None:
        with target.open("wb") as file:
            file.write(content)

    @staticmethod
    def _read_bytes(target: Path) -> bytes:
        with target.open("rb") as file:
            return file.read()

    @staticmethod
    def _move_without_overwrite(source: Path, destination: Path) -> None:
        if source.is_file():
            os.link(source, destination)
            source.unlink()
            return
        if destination.exists():
            raise FileExistsError(destination)
        source.rename(destination)

    def _list_metadata(self, target: Path) -> list[FileMetadata]:
        if not target.exists():
            return []
        if not target.is_dir():
            raise StorageValidationError("NAS list prefix must be a directory")
        items: list[FileMetadata] = []
        for file in target.rglob("*"):
            if not file.is_file():
                continue
            resolved = file.resolve(strict=False)
            if not resolved.is_relative_to(self._root_path):
                continue
            stat = resolved.stat()
            relative_path = resolved.relative_to(self._root_path).as_posix()
            content_type, _ = mimetypes.guess_type(resolved.name)
            items.append(
                FileMetadata(
                    path=StoragePath(relative_path=relative_path),
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    content_type=content_type,
                )
            )
        return sorted(items, key=lambda item: item.path.relative_path.casefold())

    def _list_directories(self, target: Path) -> list[StoragePath]:
        if not target.exists():
            return []
        if not target.is_dir():
            raise StorageValidationError("NAS list prefix must be a directory")
        items = []
        for directory in target.rglob("*"):
            if not directory.is_dir():
                continue
            resolved = directory.resolve(strict=False)
            if resolved.is_relative_to(self._root_path):
                items.append(StoragePath(relative_path=resolved.relative_to(self._root_path).as_posix()))
        return sorted(items, key=lambda item: item.relative_path.casefold())
