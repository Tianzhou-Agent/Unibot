from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.aina.scheduler import (
    ScheduledAinaDebugRequest,
    ScheduledAinaExecution,
    ScheduledAinaTask,
    ScheduledAinaTaskCreate,
    ScheduledAinaTaskUpdate,
)
from tianzhou_agent_platform.api.dependencies import repository
from tianzhou_agent_platform.core.errors import PlatformError


def create_schedule_router() -> APIRouter:
    router = APIRouter(prefix="/aina-schedules", tags=["aina-schedules"])

    @router.post("", response_model=ScheduledAinaTask, status_code=status.HTTP_201_CREATED)
    async def create_schedule(payload: ScheduledAinaTaskCreate, request: Request) -> ScheduledAinaTask:
        data_repository = repository(request)
        record = await data_repository.get_aina(payload.aina_id)
        if record.manifest.runtime.type != "remote":
            raise PlatformError("INVALID_REQUEST", "Only remote AINAs can be scheduled", status_code=422)
        installation = await data_repository.get_installation(
            tenant_id=payload.tenant_id, user_id=payload.user_id, aina_id=payload.aina_id
        )
        if installation.status != "active":
            raise PlatformError("PERMISSION_DENIED", "AINA installation is disabled", status_code=403)
        return await data_repository.create_scheduled_aina_task(payload)

    @router.get("", response_model=list[ScheduledAinaTask])
    async def list_schedules(request: Request) -> list[ScheduledAinaTask]:
        return await repository(request).list_scheduled_aina_tasks()

    @router.get("/{task_id}/executions", response_model=list[ScheduledAinaExecution])
    async def list_schedule_executions(
        task_id: str,
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[ScheduledAinaExecution]:
        data_repository = repository(request)
        await data_repository.get_scheduled_aina_task(task_id)
        return await data_repository.list_scheduled_aina_executions(task_id, limit=limit)

    @router.post("/{task_id}/run", response_model=ScheduledAinaTask)
    async def debug_schedule(
        task_id: str, payload: ScheduledAinaDebugRequest, request: Request
    ) -> ScheduledAinaTask:
        scheduler = request.app.state.aina_scheduler
        return await scheduler.run_now(task_id, input_override=payload.invocation_input())

    @router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_schedule(task_id: str, request: Request) -> Response:
        await repository(request).remove_scheduled_aina_task(task_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.patch("/{task_id}", response_model=ScheduledAinaTask)
    async def update_schedule(
        task_id: str, payload: ScheduledAinaTaskUpdate, request: Request
    ) -> ScheduledAinaTask:
        return await repository(request).update_scheduled_aina_task(task_id, payload)

    return router
