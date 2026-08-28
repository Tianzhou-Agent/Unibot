from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.api.dependencies import actor_scope, bind_actor, sandboxes
from tianzhou_agent_platform.sandbox.models import (
    SandboxEnsureRequest,
    SandboxExecution,
    SandboxExecutionRequest,
    SandboxRecord,
)


def create_sandbox_router() -> APIRouter:
    router = APIRouter(prefix="/sandboxes", tags=["sandboxes"])

    @router.post("/ensure", response_model=SandboxRecord)
    async def ensure_sandbox(payload: SandboxEnsureRequest, request: Request) -> SandboxRecord:
        return await sandboxes(request).ensure(bind_actor(request, payload))

    @router.get("/current", response_model=SandboxRecord)
    async def get_sandbox(
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
        workspace_id: str | None = Query(default=None),
    ) -> SandboxRecord:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        return await sandboxes(request).get(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_id=workspace_id,
        )

    @router.post("/execute", response_model=SandboxExecution)
    async def execute_script(
        payload: SandboxExecutionRequest,
        request: Request,
    ) -> SandboxExecution:
        return await sandboxes(request).execute(bind_actor(request, payload))

    @router.get("/executions", response_model=list[SandboxExecution])
    async def list_executions(
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
        workspace_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[SandboxExecution]:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        return await sandboxes(request).list_executions(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_id=workspace_id,
            limit=limit,
        )

    @router.post("/stop", response_model=SandboxRecord)
    async def stop_sandbox(payload: SandboxEnsureRequest, request: Request) -> SandboxRecord:
        scoped = bind_actor(request, payload)
        return await sandboxes(request).stop(
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
            workspace_id=scoped.workspace_id,
        )

    @router.delete("/current", status_code=status.HTTP_204_NO_CONTENT)
    async def reset_sandbox(
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
        workspace_id: str | None = Query(default=None),
    ) -> Response:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        await sandboxes(request).reset(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_id=workspace_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
