from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.aina.scheduler import (
    ScheduledAinaDebugRequest,
    ScheduledAinaExecution,
    ScheduledAinaTask,
    ScheduledAinaTaskCreate,
    ScheduledAinaTaskUpdate,
)
from tianzhou_agent_platform.api.dependencies import bind_actor, repository, request_actor, require_actor_ownership
from tianzhou_agent_platform.core.errors import PlatformError


def create_schedule_router() -> APIRouter:
    router = APIRouter(prefix="/aina-schedules", tags=["aina-schedules"])

    @router.post("", response_model=ScheduledAinaTask, status_code=status.HTTP_201_CREATED)
    async def create_schedule(payload: ScheduledAinaTaskCreate, request: Request) -> ScheduledAinaTask:
        scoped = bind_actor(request, payload)
        data_repository = repository(request)
        record = await data_repository.get_aina(scoped.aina_id)
        if record.manifest.runtime.type != "remote":
            raise PlatformError("INVALID_REQUEST", "Only remote AINAs can be scheduled", status_code=422)
        installation = await data_repository.get_installation(
            tenant_id=scoped.tenant_id, user_id=scoped.user_id, aina_id=scoped.aina_id
        )
        if installation.status != "active":
            raise PlatformError("PERMISSION_DENIED", "AINA installation is disabled", status_code=403)
        return await data_repository.create_scheduled_aina_task(scoped)

    @router.get("", response_model=list[ScheduledAinaTask])
    async def list_schedules(request: Request) -> list[ScheduledAinaTask]:
        tasks = await repository(request).list_scheduled_aina_tasks()
        if getattr(request.state, "actor", None) is None:
            return tasks
        actor = request_actor(request)
        return [item for item in tasks if item.user_id == actor.user_id and item.tenant_id == actor.tenant_id]

    @router.get("/{task_id}/executions", response_model=list[ScheduledAinaExecution])
    async def list_schedule_executions(
        task_id: str,
        request: Request,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[ScheduledAinaExecution]:
        data_repository = repository(request)
        task = await data_repository.get_scheduled_aina_task(task_id)
        require_actor_ownership(request, user_id=task.user_id, tenant_id=task.tenant_id)
        return await data_repository.list_scheduled_aina_executions(task_id, limit=limit)

    @router.post("/{task_id}/run", response_model=ScheduledAinaTask)
    async def debug_schedule(
        task_id: str, payload: ScheduledAinaDebugRequest, request: Request
    ) -> ScheduledAinaTask:
        task = await repository(request).get_scheduled_aina_task(task_id)
        require_actor_ownership(request, user_id=task.user_id, tenant_id=task.tenant_id)
        scheduler = request.app.state.aina_scheduler
        return await scheduler.run_now(task_id, input_override=payload.invocation_input())

    @router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_schedule(task_id: str, request: Request) -> Response:
        task = await repository(request).get_scheduled_aina_task(task_id)
        require_actor_ownership(request, user_id=task.user_id, tenant_id=task.tenant_id)
        await repository(request).remove_scheduled_aina_task(task_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.patch("/{task_id}", response_model=ScheduledAinaTask)
    async def update_schedule(
        task_id: str, payload: ScheduledAinaTaskUpdate, request: Request
    ) -> ScheduledAinaTask:
        task = await repository(request).get_scheduled_aina_task(task_id)
        require_actor_ownership(request, user_id=task.user_id, tenant_id=task.tenant_id)
        return await repository(request).update_scheduled_aina_task(task_id, payload)

    return router
