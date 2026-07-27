from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import PurePosixPath
from typing import Protocol
from uuid import uuid4

from tianzhou_agent_platform.aina.project import (
    AinaProjectRecord,
    validate_project_archive,
)
from tianzhou_agent_platform.core.errors import PlatformError, conflict
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.store.errors import StorageNotFoundError, StorageValidationError
from tianzhou_agent_platform.store.models import StoragePath
from tianzhou_agent_platform.store.nas.filesystem import NasStore


class AinaProjectArtifactStore(Protocol):
    async def write(self, path: StoragePath, payload: bytes) -> bool: ...

    async def read(self, path: StoragePath) -> bytes: ...

    async def delete(self, path: StoragePath) -> None: ...


class NasAinaProjectArtifactStore:
    def __init__(self, nas: NasStore) -> None:
        self._nas = nas

    async def write(self, path: StoragePath, payload: bytes) -> bool:
        if await self._nas.exists(path):
            await self._verify_existing(path, payload)
            return False

        temporary = StoragePath(relative_path=f"{path.relative_path}.tmp-{uuid4().hex}")
        await self._nas.write(temporary, payload, overwrite=False)
        try:
            await self._nas.move(temporary, path)
            return True
        except StorageValidationError:
            await self._nas.delete(temporary)
            if not await self._nas.exists(path):
                raise
            await self._verify_existing(path, payload)
            return False
        except Exception:
            await self._nas.delete(temporary)
            raise

    async def read(self, path: StoragePath) -> bytes:
        return await self._nas.read(path)

    async def delete(self, path: StoragePath) -> None:
        await self._nas.delete(path)

    async def _verify_existing(self, path: StoragePath, payload: bytes) -> None:
        existing = await self._nas.read(path)
        if existing != payload:
            raise _integrity_error("Stored AINA project archive does not match its content address")


class InMemoryAinaProjectArtifactStore:
    """Process-local artifact storage used when create_app has no storage backend."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._artifacts: dict[str, bytes] = {}

    async def write(self, path: StoragePath, payload: bytes) -> bool:
        async with self._lock:
            existing = self._artifacts.get(path.relative_path)
            if existing is not None:
                if existing != payload:
                    raise _integrity_error("Stored AINA project archive does not match its content address")
                return False
            self._artifacts[path.relative_path] = bytes(payload)
            return True

    async def read(self, path: StoragePath) -> bytes:
        async with self._lock:
            payload = self._artifacts.get(path.relative_path)
            if payload is None:
                raise StorageNotFoundError("AINA project archive was not found")
            return bytes(payload)

    async def delete(self, path: StoragePath) -> None:
        async with self._lock:
            self._artifacts.pop(path.relative_path, None)


class AinaProjectService:
    def __init__(self, repository: InMemoryRepository, artifacts: AinaProjectArtifactStore) -> None:
        self._repository = repository
        self._artifacts = artifacts

    async def import_project(
        self,
        payload: bytes,
        *,
        source_filename: str | None,
        user_id: str,
        tenant_id: str,
    ) -> AinaProjectRecord:
        report = validate_project_archive(payload)
        if report.manifest.runtime.type != "managed":
            raise PlatformError(
                "INVALID_REQUEST",
                "Only managed AINA projects can be imported; remote AINAs use the registration API",
                status_code=422,
                source="aina_project",
            )

        aina_id = report.manifest.aina.id
        version = report.manifest.aina.version
        record = AinaProjectRecord(
            id=_project_record_id(
                aina_id=aina_id,
                version=version,
                user_id=user_id,
                tenant_id=tenant_id,
            ),
            user_id=user_id,
            tenant_id=tenant_id,
            source_filename=_source_filename(source_filename, aina_id=aina_id, version=version),
            archive_sha256=report.archive_sha256,
            size_bytes=report.size_bytes,
            uncompressed_size_bytes=report.uncompressed_size_bytes,
            file_count=report.file_count,
            manifest=report.manifest,
        )
        reserved = await self._repository.create_aina_project(record)
        if reserved.archive_sha256 != report.archive_sha256:
            raise _archive_conflict(aina_id, version)
        if reserved.status == "validated":
            await self._read_verified(reserved)
            return reserved

        await self._artifacts.write(_artifact_path(reserved), payload)
        return await self._repository.mark_aina_project_validated(
            reserved.id,
            archive_sha256=reserved.archive_sha256,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    async def list_projects(self, *, user_id: str, tenant_id: str) -> list[AinaProjectRecord]:
        return await self._repository.list_aina_projects(user_id=user_id, tenant_id=tenant_id)

    async def get_project(self, project_id: str, *, user_id: str, tenant_id: str) -> AinaProjectRecord:
        return await self._repository.get_aina_project(
            project_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    async def get_archive(self, project_id: str, *, user_id: str, tenant_id: str) -> tuple[AinaProjectRecord, bytes]:
        record = await self._repository.get_aina_project(
            project_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        if record.status == "importing":
            raise conflict("AINA project import has not completed")
        return record, await self._read_verified(record)

    async def delete_project(self, project_id: str, *, user_id: str, tenant_id: str) -> None:
        record = await self._repository.get_aina_project(
            project_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        if record.status == "deployed":
            raise conflict("Undeploy the AINA project before deleting it")
        await self._artifacts.delete(_artifact_path(record))
        await self._repository.remove_aina_project(
            project_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    async def _read_verified(self, record: AinaProjectRecord) -> bytes:
        payload = await self._artifacts.read(_artifact_path(record))
        if len(payload) != record.size_bytes or hashlib.sha256(payload).hexdigest() != record.archive_sha256:
            raise _integrity_error("AINA project archive failed its integrity check")
        return payload


def _artifact_path(record: AinaProjectRecord) -> StoragePath:
    return StoragePath(relative_path=f"aina-projects/{record.id}/{record.archive_sha256}.aina.zip")


def _project_record_id(*, aina_id: str, version: str, user_id: str, tenant_id: str) -> str:
    identity = json.dumps([tenant_id, user_id, aina_id, version], ensure_ascii=False, separators=(",", ":"))
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"aina_project_{digest[:32]}"


def _source_filename(value: str | None, *, aina_id: str, version: str) -> str:
    filename = PurePosixPath((value or "").replace("\\", "/")).name
    filename = "".join(character for character in filename if 32 <= ord(character) != 127).strip()
    if not filename:
        filename = f"{aina_id}-{version}.aina.zip"
    return filename[:255]


def _archive_conflict(aina_id: str, version: str) -> PlatformError:
    return conflict(f"AINA project {aina_id!r} version {version!r} was already imported with different content")


def _integrity_error(message: str) -> PlatformError:
    return PlatformError(
        "INTEGRITY_CHECK_FAILED",
        message,
        status_code=500,
        source="aina_project",
    )
