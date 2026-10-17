"""AINA Host API (design §8): the routes an AINA's MCP server calls back into Unibot with its context token.

Authorization never comes from a user session: every request carries ``Authorization: Bearer <context token>``.
A request is allowed only while the token is valid, the installation it names is still active, and the scope is
both in the token and still granted to the installation, so uninstalling or revoking a scope takes effect at once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status

from tianzhou_agent_platform.aina.package.storage import MAX_LIST_ITEMS, storage_item
from tianzhou_agent_platform.aina.package.tokens import VerifiedToken, invalid_token
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation
from tianzhou_agent_platform.api.dependencies import aina_packages, repository
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


async def host_caller(request: Request) -> HostCaller:
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise invalid_token()
    verified = aina_packages(request).tokens.verify(token.strip())
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
    audit.info(
        "aina host api %s",
        action,
        extra={
            "aina_id": caller.installation.aina_id,
            "scope": scope,
            "pairwise_user": caller.token.claims.get("sub"),
            "invocation_id": caller.token.claims.get("invocation_id"),
            "allowed": granted,
        },
    )
    if not granted:
        raise PlatformError(
            "PERMISSION_DENIED",
            f"The {scope!r} permission is not granted to this AINA",
            status_code=403,
            source="aina_host",
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

    return router
