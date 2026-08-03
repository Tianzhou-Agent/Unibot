from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.aina.document.task_models import (
    DocumentDraftAiRevision,
    DocumentDraftUpdate,
    DocumentEditTask,
    DocumentEditTaskActor,
    DocumentEditTaskCreate,
    DocumentEditTaskListResponse,
)
from tianzhou_agent_platform.api.dependencies import actor_scope, bind_actor, document_edit_tasks


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
    ) -> DocumentEditTaskListResponse:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        items = await document_edit_tasks(request).list_tasks(
            name,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        return DocumentEditTaskListResponse(items=items, total=len(items))

    @router.get("/document-edit-tasks/{task_id}", response_model=DocumentEditTask)
    async def get_task(
        task_id: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
    ) -> DocumentEditTask:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        return await document_edit_tasks(request).get_task(
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
        return await document_edit_tasks(request).update_draft(
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
        return await document_edit_tasks(request).request_ai_revision(
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
        return await document_edit_tasks(request).merge_section(
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
        return await document_edit_tasks(request).abandon_section(
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
        return await document_edit_tasks(request).retry_failed(
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
        return await document_edit_tasks(request).abandon_task(
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
    ) -> Response:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        await document_edit_tasks(request).delete_task(
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
        return await document_edit_tasks(request).merge_task(
            task_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
        )

    return router
