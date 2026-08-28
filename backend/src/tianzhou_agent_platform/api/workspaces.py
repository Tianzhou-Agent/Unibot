from fastapi import APIRouter, Request, status

from tianzhou_agent_platform.api.dependencies import (
    actor_scope,
    bind_actor,
    repository,
    require_actor_ownership,
)
from tianzhou_agent_platform.core.workspace import Workspace, WorkspaceCreate, WorkspaceUpdate


def create_workspace_router() -> APIRouter:
    router = APIRouter()

    @router.post("/workspaces", response_model=Workspace, status_code=status.HTTP_201_CREATED)
    async def create_workspace(payload: WorkspaceCreate, request: Request) -> Workspace:
        return await repository(request).create_workspace(bind_actor(request, payload))

    @router.get("/workspaces", response_model=list[Workspace])
    async def list_workspaces(
        request: Request,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[Workspace]:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        return await repository(request).list_workspaces(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )

    @router.get("/workspaces/{workspace_id}", response_model=Workspace)
    async def get_workspace(workspace_id: str, request: Request) -> Workspace:
        workspace = await repository(request).get_workspace(workspace_id)
        require_actor_ownership(request, user_id=workspace.user_id, tenant_id=workspace.tenant_id)
        return workspace

    @router.patch("/workspaces/{workspace_id}", response_model=Workspace)
    async def update_workspace(
        workspace_id: str,
        payload: WorkspaceUpdate,
        request: Request,
    ) -> Workspace:
        workspace = await repository(request).get_workspace(workspace_id)
        require_actor_ownership(request, user_id=workspace.user_id, tenant_id=workspace.tenant_id)
        return await repository(request).update_workspace(workspace_id, payload)

    return router
