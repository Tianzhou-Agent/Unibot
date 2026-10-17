"""AINA Host API (design §8): the routes an AINA's MCP server calls back into Unibot with its context token.

Authorization never comes from a user session: every request carries ``Authorization: Bearer <context token>``.
A request is allowed only while the token is valid, the installation it names is still active, and the scope is
both in the token and still granted to the installation, so uninstalling or revoking a scope takes effect at once.

Task callbacks (design §7.3) need no scope: a task id is looked up within the caller's installation only. A task's
token may be refreshed after it expired, but only while the task is live and only with a token of the invocation
that started it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from pydantic import ConfigDict, Field

from tianzhou_agent_platform.aina.package.service import content_text
from tianzhou_agent_platform.aina.package.storage import MAX_LIST_ITEMS, storage_item
from tianzhou_agent_platform.aina.package.tasks import MAX_MESSAGE_CHARS, AinaTask
from tianzhou_agent_platform.aina.package.tokens import MAX_TOKEN_SECONDS, VerifiedToken, invalid_token
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation
from tianzhou_agent_platform.api.dependencies import aina_packages, aina_tasks, repository
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.errors import PlatformError, not_found
from tianzhou_agent_platform.store.memory_repository import installation_record_id

HOST_API_PREFIX = "/aina-host/v1"
JWKS_PATH = "/.well-known/aina-host-jwks.json"

audit = logging.getLogger("tianzhou_agent_platform.aina.host_api")


@dataclass(frozen=True, slots=True)
class HostCaller:
    token: VerifiedToken
    installation: AinaInstallation

    @property
    def installation_key(self) -> str:
        return installation_record_id(
            self.installation.tenant_id, self.installation.user_id, self.installation.aina_id
        )


class StorageValue(StrictModel):
    key: str
    value: Any
    updated_at: datetime


class StorageList(StrictModel):
    items: list[StorageValue]


class TaskProgress(StrictModel):
    progress: float = Field(ge=0)
    total: float | None = Field(default=None, gt=0)
    message: str | None = Field(default=None, max_length=MAX_MESSAGE_CHARS)


class TaskCompletion(StrictModel):
    """An MCP-shaped result: ``content`` blocks for the user and model, optional ``structuredContent``."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    content: list[dict[str, Any]] = Field(default_factory=list)
    structured_content: dict[str, Any] | None = Field(default=None, alias="structuredContent")


class TaskFailure(StrictModel):
    error: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class TaskState(StrictModel):
    """What the AINA sees of its task: never the host's raw user, tenant or conversation ids."""

    id: str
    title: str
    status: str
    progress: float | None
    total: float | None
    message: str | None
    updated_at: datetime


class TokenRefresh(StrictModel):
    task_id: str = Field(min_length=1, max_length=128)


class RefreshedToken(StrictModel):
    token: str
    expires_in: int


async def host_caller(request: Request) -> HostCaller:
    return await _caller(request, allow_expired=False)


async def refresh_caller(request: Request) -> HostCaller:
    return await _caller(request, allow_expired=True)


async def _caller(request: Request, *, allow_expired: bool) -> HostCaller:
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise invalid_token()
    verified = aina_packages(request).tokens.verify(token.strip(), allow_expired=allow_expired)
    reference = verified.reference
    try:
        installation = await repository(request).get_installation(
            tenant_id=reference.tenant_id,
            user_id=reference.user_id,
            aina_id=reference.aina_id,
        )
    except PlatformError as exc:
        if exc.code == "RESOURCE_NOT_FOUND":
            raise invalid_token() from exc  # uninstalled: every token of the installation is revoked
        raise
    if installation.status != "active":
        raise invalid_token()
    return HostCaller(token=verified, installation=installation)


def require_scope(caller: HostCaller, scope: str, action: str) -> None:
    granted = scope in caller.token.scopes and scope in caller.installation.granted_permissions
    _audit(caller, action, scope=scope, allowed=granted)
    if not granted:
        raise PlatformError(
            "PERMISSION_DENIED",
            f"The {scope!r} permission is not granted to this AINA",
            status_code=403,
            source="aina_host",
        )


def _audit(caller: HostCaller, action: str, *, scope: str, allowed: bool) -> None:
    audit.info(
        "aina host api %s",
        action,
        extra={
            "aina_id": caller.installation.aina_id,
            "scope": scope,
            "pairwise_user": caller.token.claims.get("sub"),
            "invocation_id": caller.token.claims.get("invocation_id"),
            "allowed": allowed,
        },
    )


def _task_state(task: AinaTask) -> TaskState:
    return TaskState(
        id=task.external_id,
        title=task.title,
        status=task.status,
        progress=task.progress,
        total=task.total,
        message=task.message,
        updated_at=task.updated_at,
    )


def create_aina_host_router() -> APIRouter:
    router = APIRouter(tags=["aina-host"])

    @router.get(JWKS_PATH)
    async def aina_host_jwks(request: Request) -> dict[str, Any]:
        """Public keys that verify AINA context tokens."""
        return aina_packages(request).tokens.jwks()

    @router.get(f"{HOST_API_PREFIX}/storage/kv", response_model=StorageList)
    async def list_storage(
        request: Request,
        prefix: str = Query(default="", max_length=512),
        limit: int = Query(default=100, ge=1, le=MAX_LIST_ITEMS),
        caller: HostCaller = Depends(host_caller),
    ) -> StorageList:
        require_scope(caller, "storage", "storage.list")
        items = await repository(request).list_aina_storage_items(caller.installation_key, prefix=prefix, limit=limit)
        return StorageList(items=[StorageValue(key=i.key, value=i.value, updated_at=i.updated_at) for i in items])

    @router.get(f"{HOST_API_PREFIX}/storage/kv/{{key:path}}", response_model=StorageValue)
    async def get_storage(key: str, request: Request, caller: HostCaller = Depends(host_caller)) -> StorageValue:
        require_scope(caller, "storage", "storage.get")
        item = await repository(request).get_aina_storage_item(caller.installation_key, key)
        if item is None:
            raise not_found("Storage key", key)
        return StorageValue(key=item.key, value=item.value, updated_at=item.updated_at)

    @router.put(f"{HOST_API_PREFIX}/storage/kv/{{key:path}}", response_model=StorageValue)
    async def put_storage(
        key: str,
        request: Request,
        value: Any = Body(..., embed=True),
        caller: HostCaller = Depends(host_caller),
    ) -> StorageValue:
        require_scope(caller, "storage", "storage.put")
        item = storage_item(
            installation_key=caller.installation_key,
            aina_id=caller.installation.aina_id,
            key=key,
            value=value,
        )
        stored = await repository(request).put_aina_storage_item(item)
        return StorageValue(key=stored.key, value=stored.value, updated_at=stored.updated_at)

    @router.delete(f"{HOST_API_PREFIX}/storage/kv/{{key:path}}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_storage(key: str, request: Request, caller: HostCaller = Depends(host_caller)) -> Response:
        require_scope(caller, "storage", "storage.delete")
        if not await repository(request).delete_aina_storage_item(caller.installation_key, key):
            raise not_found("Storage key", key)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(f"{HOST_API_PREFIX}/tasks/{{task_id}}/progress", response_model=TaskState)
    async def task_progress(
        task_id: str, payload: TaskProgress, request: Request, caller: HostCaller = Depends(host_caller)
    ) -> TaskState:
        _audit(caller, "tasks.progress", scope="tasks", allowed=True)
        task = await aina_tasks(request).progress(
            caller.installation_key,
            task_id,
            progress=payload.progress,
            total=payload.total,
            message=payload.message,
        )
        return _task_state(task)

    @router.post(f"{HOST_API_PREFIX}/tasks/{{task_id}}/complete", response_model=TaskState)
    async def task_complete(
        task_id: str, payload: TaskCompletion, request: Request, caller: HostCaller = Depends(host_caller)
    ) -> TaskState:
        _audit(caller, "tasks.complete", scope="tasks", allowed=True)
        task = await aina_tasks(request).complete(
            caller.installation_key,
            task_id,
            result=content_text(payload.content),
            structured_content=payload.structured_content,
        )
        return _task_state(task)

    @router.post(f"{HOST_API_PREFIX}/tasks/{{task_id}}/fail", response_model=TaskState)
    async def task_fail(
        task_id: str, payload: TaskFailure, request: Request, caller: HostCaller = Depends(host_caller)
    ) -> TaskState:
        _audit(caller, "tasks.fail", scope="tasks", allowed=True)
        return _task_state(await aina_tasks(request).fail(caller.installation_key, task_id, error=payload.error))

    @router.post(f"{HOST_API_PREFIX}/token/refresh", response_model=RefreshedToken)
    async def token_refresh(
        payload: TokenRefresh, request: Request, caller: HostCaller = Depends(refresh_caller)
    ) -> RefreshedToken:
        task = await aina_tasks(request).live(caller.installation_key, payload.task_id)
        bound = caller.token.claims.get("invocation_id") == task.invocation_id
        _audit(caller, "token.refresh", scope="tasks", allowed=bound)
        if not bound:
            raise invalid_token()  # only a token of the invocation that started the task refreshes it
        claims = {
            key: value
            for key, value in caller.token.claims.items()
            if key not in {"iss", "aud", "iat", "exp", "jti", "ref"}
        }
        # Scopes revoked since the token was minted are not carried over.
        claims["scopes"] = sorted(caller.token.scopes & set(caller.installation.granted_permissions))
        token = aina_packages(request).tokens.mint(reference=caller.token.reference, claims=claims)
        return RefreshedToken(token=token, expires_in=MAX_TOKEN_SECONDS)

    return router
