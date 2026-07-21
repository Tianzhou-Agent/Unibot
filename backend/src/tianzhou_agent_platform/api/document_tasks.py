from fastapi import APIRouter, Query, Request, status

from tianzhou_agent_platform.aina.document.task_models import (
    DocumentDraftAiRevision,
    DocumentDraftUpdate,
    DocumentEditTask,
    DocumentEditTaskActor,
    DocumentEditTaskCreate,
    DocumentEditTaskListResponse,
)
from tianzhou_agent_platform.api.dependencies import document_edit_tasks


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
        return await document_edit_tasks(request).create_task(name, payload)

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
        items = await document_edit_tasks(request).list_tasks(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        return DocumentEditTaskListResponse(items=items, total=len(items))

    @router.get("/document-edit-tasks/{task_id}", response_model=DocumentEditTask)
    async def get_task(
        task_id: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
    ) -> DocumentEditTask:
        return await document_edit_tasks(request).get_task(
            task_id,
            user_id=user_id,
            tenant_id=tenant_id,
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
        return await document_edit_tasks(request).update_draft(
            task_id,
            section_id,
            payload.content,
            payload.expected_draft_revision,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
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
        return await document_edit_tasks(request).request_ai_revision(
            task_id,
            section_id,
            payload.instruction,
            payload.expected_draft_revision,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
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
        return await document_edit_tasks(request).retry_failed(
            task_id,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
        )

    @router.post(
        "/document-edit-tasks/{task_id}/merge",
        response_model=DocumentEditTask,
    )
    async def merge_task(
        task_id: str,
        payload: DocumentEditTaskActor,
        request: Request,
    ) -> DocumentEditTask:
        return await document_edit_tasks(request).merge_task(
            task_id,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
        )

    return router
