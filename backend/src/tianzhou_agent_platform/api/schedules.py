from fastapi import APIRouter, Query, Request, Response, status
from pydantic import ValidationError

from tianzhou_agent_platform.aina.scheduler import (
    ScheduledAinaDebugRequest,
    ScheduledAinaExecution,
    ScheduledAinaTask,
    ScheduledAinaTaskCreate,
    ScheduledAinaTaskUpdate,
)
from tianzhou_agent_platform.api.dependencies import bind_actor, repository, request_actor, require_actor_ownership
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.memory_repository import updated_scheduled_aina_task


def create_schedule_router() -> APIRouter:
    router = APIRouter(prefix="/aina-schedules", tags=["aina-schedules"])

    @router.post("", response_model=ScheduledAinaTask, status_code=status.HTTP_201_CREATED)
    async def create_schedule(payload: ScheduledAinaTaskCreate, request: Request) -> ScheduledAinaTask:
        scoped = bind_actor(request, payload)
        data_repository = repository(request)
        await request.app.state.aina_scheduler.validate_task(scoped)
        return await data_repository.create_scheduled_aina_task(scoped)

    @router.get("", response_model=list[ScheduledAinaTask])
    async def list_schedules(request: Request) -> list[ScheduledAinaTask]:
        if getattr(request.state, "actor", None) is None:
            return await repository(request).list_scheduled_aina_tasks()
        actor = request_actor(request)
        return await repository(request).list_scheduled_aina_tasks(user_id=actor.user_id, tenant_id=actor.tenant_id)

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
        try:
            merged = updated_scheduled_aina_task(task, payload)
        except ValidationError as exc:
            raise PlatformError("INVALID_REQUEST", "The updated schedule is invalid", status_code=422) from exc
        # Pausing must remain possible even if the previously selected app is now unavailable.
        if payload.enabled is not False or payload.model_fields_set - {"enabled"}:
            await request.app.state.aina_scheduler.validate_task(merged)
        return await repository(request).update_scheduled_aina_task(task_id, payload)

    return router
