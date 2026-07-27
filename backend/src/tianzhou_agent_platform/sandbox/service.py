from __future__ import annotations

import asyncio
import hashlib
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.sandbox.drivers import SandboxDriver
from tianzhou_agent_platform.sandbox.models import (
    SandboxEnsureRequest,
    SandboxExecution,
    SandboxExecutionRequest,
    SandboxRecord,
)


class SandboxService:
    def __init__(
        self,
        repository: InMemoryRepository,
        driver: SandboxDriver,
        *,
        default_image: str,
    ) -> None:
        self.repository = repository
        self.driver = driver
        self.default_image = default_image
        self._actor_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()

    async def ensure(self, request: SandboxEnsureRequest) -> SandboxRecord:
        async with self._actor_lease(request.user_id, request.tenant_id):
            return await self._ensure_unlocked(request)

    async def _ensure_unlocked(self, request: SandboxEnsureRequest) -> SandboxRecord:
        try:
            sandbox = await self.repository.get_sandbox_for_actor(
                user_id=request.user_id,
                tenant_id=request.tenant_id,
            )
        except PlatformError as exc:
            if exc.code != "RESOURCE_NOT_FOUND":
                raise
            actor_digest = _actor_digest(request.tenant_id, request.user_id)
            sandbox = SandboxRecord(
                id=f"sandbox_{actor_digest}",
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                image=self.default_image,
                driver=self.driver.name,  # type: ignore[arg-type]
                runtime_name=f"unibot-{actor_digest}",
                workspace="/workspace",
            )
        else:
            sandbox = sandbox.model_copy(update={"image": self.default_image})
        state = await self.driver.ensure(sandbox)
        now = datetime.now(UTC)
        updated = sandbox.model_copy(
            update={
                "image": sandbox.image,
                "status": state.status,
                "runtime_name": state.runtime_name,
                "workspace": state.workspace,
                "endpoint": state.endpoint,
                "last_error": state.error,
                "updated_at": now,
                "last_activity_at": now,
            },
            deep=True,
        )
        return await self.repository.put_sandbox(updated)

    async def get(self, *, user_id: str, tenant_id: str) -> SandboxRecord:
        return await self.repository.get_sandbox_for_actor(user_id=user_id, tenant_id=tenant_id)

    async def execute(self, request: SandboxExecutionRequest) -> SandboxExecution:
        async with self._actor_lease(request.user_id, request.tenant_id):
            sandbox = await self._ensure_unlocked(
                SandboxEnsureRequest(
                    user_id=request.user_id,
                    tenant_id=request.tenant_id,
                )
            )
            execution = SandboxExecution(
                id=f"execution_{uuid4().hex}",
                sandbox_id=sandbox.id,
                user_id=request.user_id,
                tenant_id=request.tenant_id,
                language=request.language,
                script=request.script,
                working_directory=request.working_directory,
            )
            await self.repository.put_sandbox_execution(execution)
            await self.repository.put_sandbox(
                sandbox.model_copy(update={"status": "busy", "updated_at": datetime.now(UTC)})
            )
            try:
                result = await self.driver.execute(sandbox, request)
                completed = execution.model_copy(
                    update={
                        **result.model_dump(),
                        "finished_at": datetime.now(UTC),
                    },
                    deep=True,
                )
            except PlatformError as exc:
                completed = execution.model_copy(
                    update={
                        "status": "failed",
                        "stderr": exc.user_message or exc.message,
                        "finished_at": datetime.now(UTC),
                    }
                )
                await self.repository.put_sandbox_execution(completed)
                await self.repository.put_sandbox(
                    sandbox.model_copy(
                        update={
                            "status": "error",
                            "last_error": exc.user_message or exc.message,
                            "updated_at": datetime.now(UTC),
                        }
                    )
                )
                raise
            await self.repository.put_sandbox_execution(completed)
            await self.repository.put_sandbox(
                sandbox.model_copy(
                    update={
                        "status": "ready",
                        "last_error": None,
                        "updated_at": datetime.now(UTC),
                        "last_activity_at": datetime.now(UTC),
                    }
                )
            )
            return completed

    async def write_file(
        self,
        *,
        user_id: str,
        tenant_id: str,
        path: str,
        content: bytes,
        overwrite: bool = True,
    ) -> None:
        normalized = _workspace_file_path(path)
        async with self._actor_lease(user_id, tenant_id):
            sandbox = await self._ensure_unlocked(SandboxEnsureRequest(user_id=user_id, tenant_id=tenant_id))
            await self.driver.write_file(sandbox, normalized, content, overwrite=overwrite)

    async def read_file(self, *, user_id: str, tenant_id: str, path: str) -> bytes:
        normalized = _workspace_file_path(path)
        async with self._actor_lease(user_id, tenant_id):
            sandbox = await self._ensure_unlocked(SandboxEnsureRequest(user_id=user_id, tenant_id=tenant_id))
            return await self.driver.read_file(sandbox, normalized)

    async def delete_file(self, *, user_id: str, tenant_id: str, path: str) -> None:
        normalized = _workspace_file_path(path)
        async with self._actor_lease(user_id, tenant_id):
            sandbox = await self._ensure_unlocked(SandboxEnsureRequest(user_id=user_id, tenant_id=tenant_id))
            await self.driver.delete_file(sandbox, normalized)

    async def list_executions(
        self,
        *,
        user_id: str,
        tenant_id: str,
        limit: int = 50,
    ) -> list[SandboxExecution]:
        return await self.repository.list_sandbox_executions(
            user_id=user_id,
            tenant_id=tenant_id,
            limit=limit,
        )

    async def stop(self, *, user_id: str, tenant_id: str) -> SandboxRecord:
        async with self._actor_lease(user_id, tenant_id):
            sandbox = await self.get(user_id=user_id, tenant_id=tenant_id)
            state = await self.driver.stop(sandbox)
            return await self.repository.put_sandbox(
                sandbox.model_copy(
                    update={
                        "status": state.status,
                        "endpoint": state.endpoint,
                        "last_error": state.error,
                        "updated_at": datetime.now(UTC),
                    }
                )
            )

    async def reset(self, *, user_id: str, tenant_id: str) -> None:
        async with self._actor_lease(user_id, tenant_id):
            sandbox = await self.get(user_id=user_id, tenant_id=tenant_id)
            await self.driver.reset(sandbox)
            await self.repository.remove_sandbox(sandbox.id)

    async def aclose(self) -> None:
        await self.driver.aclose()

    @asynccontextmanager
    async def _actor_lease(self, user_id: str, tenant_id: str) -> AsyncIterator[None]:
        key = hashlib.sha256(f"{tenant_id}:{user_id}".encode()).hexdigest()
        local_lock = self._actor_locks.setdefault(key, asyncio.Lock())
        async with local_lock:
            stores = getattr(self.repository, "stores", None)
            if stores is None or not hasattr(stores.redis, "lease"):
                yield
                return
            deadline = asyncio.get_running_loop().time() + 10
            while asyncio.get_running_loop().time() < deadline:
                async with stores.redis.lease(
                    "sandbox-actor-operation",
                    key,
                    ttl_seconds=60,
                ) as acquired:
                    if acquired:
                        yield
                        return
                await asyncio.sleep(0.1)
            raise PlatformError(
                "CONFLICT",
                "Another sandbox operation is already running for this user",
                status_code=409,
                retryable=True,
                source="sandbox",
            )


def _actor_digest(tenant_id: str, user_id: str) -> str:
    return hashlib.sha256(f"{tenant_id}:{user_id}".encode()).hexdigest()[:20]


def _workspace_file_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or "\x00" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise PlatformError("PERMISSION_DENIED", "Sandbox file path escapes the workspace", status_code=403)
    return normalized
