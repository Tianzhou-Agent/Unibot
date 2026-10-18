"""Developer tokens for the ``aina`` CLI (design §12): created, listed and revoked with a signed-in session."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from tianzhou_agent_platform.api.dependencies import auth, repository, request_actor
from tianzhou_agent_platform.auth.models import CreatedDeveloperToken, DeveloperToken, DeveloperTokenCreate
from tianzhou_agent_platform.core.errors import not_found


def create_developer_token_router() -> APIRouter:
    router = APIRouter(prefix="/developer-tokens", tags=["developer-tokens"])

    @router.get("", response_model=list[DeveloperToken])
    async def list_developer_tokens(request: Request) -> list[DeveloperToken]:
        actor = request_actor(request)
        return await repository(request).list_developer_tokens(tenant_id=actor.tenant_id, user_id=actor.user_id)

    @router.post("", response_model=CreatedDeveloperToken, status_code=status.HTTP_201_CREATED)
    async def create_developer_token(payload: DeveloperTokenCreate, request: Request) -> CreatedDeveloperToken:
        actor = request_actor(request)
        return await auth(request).create_developer_token(payload, user_id=actor.user_id, tenant_id=actor.tenant_id)

    @router.delete("/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def revoke_developer_token(token_id: str, request: Request) -> Response:
        actor = request_actor(request)
        if not await repository(request).delete_developer_token(
            token_id, tenant_id=actor.tenant_id, user_id=actor.user_id
        ):
            raise not_found("Developer token", token_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
