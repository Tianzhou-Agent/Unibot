from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.api.dependencies import sandboxes
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
        return await sandboxes(request).ensure(payload)

    @router.get("/current", response_model=SandboxRecord)
    async def get_sandbox(
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
    ) -> SandboxRecord:
        return await sandboxes(request).get(user_id=user_id, tenant_id=tenant_id)

    @router.post("/execute", response_model=SandboxExecution)
    async def execute_script(
        payload: SandboxExecutionRequest,
        request: Request,
    ) -> SandboxExecution:
        return await sandboxes(request).execute(payload)

    @router.get("/executions", response_model=list[SandboxExecution])
    async def list_executions(
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[SandboxExecution]:
        return await sandboxes(request).list_executions(
            user_id=user_id,
            tenant_id=tenant_id,
            limit=limit,
        )

    @router.post("/stop", response_model=SandboxRecord)
    async def stop_sandbox(payload: SandboxEnsureRequest, request: Request) -> SandboxRecord:
        return await sandboxes(request).stop(user_id=payload.user_id, tenant_id=payload.tenant_id)

    @router.delete("/current", status_code=status.HTTP_204_NO_CONTENT)
    async def reset_sandbox(
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
    ) -> Response:
        await sandboxes(request).reset(user_id=user_id, tenant_id=tenant_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router
