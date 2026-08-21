from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.aina.document.task_models import (
    DocumentDraftAiRevision,
    DocumentDraftUpdate,
    DocumentEditTask,
    DocumentEditTaskActor,
    DocumentEditTaskCreate,
    DocumentEditTaskListResponse,
)
from tianzhou_agent_platform.aina.document.task_service import DocumentEditTaskService
from tianzhou_agent_platform.api.dependencies import actor_scope, bind_actor, document_edit_tasks
from tianzhou_agent_platform.core.errors import PlatformError


def create_document_task_router() -> APIRouter:
    router = APIRouter()

    @router.post(
        "/documents/{name:path}/edit-tasks",
        response_model=DocumentEditTask,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def create_task(
        name: str,
        payload: DocumentEditTaskCreate,
        request: Request,
    ) -> DocumentEditTask:
        return await document_edit_tasks(request).create_task(name, bind_actor(request, payload))

    @router.get(
        "/documents/{name:path}/edit-tasks",
        response_model=DocumentEditTaskListResponse,
    )
    async def list_tasks(
        name: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
        workspace_id: str | None = Query(default=None),
    ) -> DocumentEditTaskListResponse:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        items = await document_edit_tasks(request).list_tasks(
            name,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            workspace_id=workspace_id,
        )
        return DocumentEditTaskListResponse(items=items, total=len(items))

    @router.get("/document-edit-tasks/{task_id}", response_model=DocumentEditTask)
    async def get_task(
        task_id: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
        workspace_id: str | None = Query(default=None),
    ) -> DocumentEditTask:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        service = document_edit_tasks(request)
        await _require_task_workspace(
            service,
            task_id,
            workspace_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        return await service.get_task(
            task_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )

    @router.patch(
        "/document-edit-tasks/{task_id}/sections/{section_id}",
        response_model=DocumentEditTask,
    )
    async def update_draft(
        task_id: str,
        section_id: str,
        payload: DocumentDraftUpdate,
        request: Request,
    ) -> DocumentEditTask:
        scoped = bind_actor(request, payload)
        service = document_edit_tasks(request)
        await _require_task_workspace(
            service,
            task_id,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await service.update_draft(
            task_id,
            section_id,
            scoped.content,
            scoped.expected_draft_revision,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )

    @router.post(
        "/document-edit-tasks/{task_id}/sections/{section_id}/ai-revise",
        response_model=DocumentEditTask,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def revise_draft(
        task_id: str,
        section_id: str,
        payload: DocumentDraftAiRevision,
        request: Request,
    ) -> DocumentEditTask:
        scoped = bind_actor(request, payload)
        service = document_edit_tasks(request)
        await _require_task_workspace(
            service,
            task_id,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await service.request_ai_revision(
            task_id,
            section_id,
            scoped.instruction,
            scoped.expected_draft_revision,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )

    @router.post(
        "/document-edit-tasks/{task_id}/sections/{section_id}/merge",
        response_model=DocumentEditTask,
    )
    async def merge_section(
        task_id: str,
        section_id: str,
        payload: DocumentEditTaskActor,
        request: Request,
    ) -> DocumentEditTask:
        scoped = bind_actor(request, payload)
        service = document_edit_tasks(request)
        await _require_task_workspace(
            service,
            task_id,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await service.merge_section(
            task_id,
            section_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )

    @router.post(
        "/document-edit-tasks/{task_id}/sections/{section_id}/abandon",
        response_model=DocumentEditTask,
    )
    async def abandon_section(
        task_id: str,
        section_id: str,
        payload: DocumentEditTaskActor,
        request: Request,
    ) -> DocumentEditTask:
        scoped = bind_actor(request, payload)
        service = document_edit_tasks(request)
        await _require_task_workspace(
            service,
            task_id,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await service.abandon_section(
            task_id,
            section_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )

    @router.post(
        "/document-edit-tasks/{task_id}/retry",
        response_model=DocumentEditTask,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def retry_task(
        task_id: str,
        payload: DocumentEditTaskActor,
        request: Request,
    ) -> DocumentEditTask:
        scoped = bind_actor(request, payload)
        service = document_edit_tasks(request)
        await _require_task_workspace(
            service,
            task_id,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await service.retry_failed(
            task_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )

    @router.post(
        "/document-edit-tasks/{task_id}/abandon",
        response_model=DocumentEditTask,
    )
    async def abandon_task(
        task_id: str,
        payload: DocumentEditTaskActor,
        request: Request,
    ) -> DocumentEditTask:
        scoped = bind_actor(request, payload)
        service = document_edit_tasks(request)
        await _require_task_workspace(
            service,
            task_id,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await service.abandon_task(
            task_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )

    @router.delete(
        "/document-edit-tasks/{task_id}",
        status_code=status.HTTP_204_NO_CONTENT,
    )
    async def delete_task(
        task_id: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
        workspace_id: str | None = Query(default=None),
    ) -> Response:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        service = document_edit_tasks(request)
        await _require_task_workspace(
            service,
            task_id,
            workspace_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        await service.delete_task(
            task_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post(
        "/document-edit-tasks/{task_id}/merge",
        response_model=DocumentEditTask,
    )
    async def merge_task(
        task_id: str,
        payload: DocumentEditTaskActor,
        request: Request,
    ) -> DocumentEditTask:
        scoped = bind_actor(request, payload)
        service = document_edit_tasks(request)
        await _require_task_workspace(
            service,
            task_id,
            scoped.workspace_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )
        return await service.merge_task(
            task_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )

    return router


async def _require_task_workspace(
    service: DocumentEditTaskService,
    task_id: str,
    workspace_id: str | None,
    *,
    user_id: str,
    tenant_id: str,
) -> None:
    task = await service.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
    if task.workspace_id != workspace_id:
        raise PlatformError(
            "CONFLICT",
            "Document task does not belong to the requested workspace",
            status_code=409,
        )
