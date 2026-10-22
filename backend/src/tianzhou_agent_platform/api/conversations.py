from fastapi import APIRouter, File, Request, Response, UploadFile, status

from tianzhou_agent_platform.aina.package.tasks import AinaTask
from tianzhou_agent_platform.api.dependencies import (
    actor_scope,
    aina_tasks,
    bind_actor,
    chat_attachments,
    chat_service,
    conversation_service,
    require_actor_ownership,
)
from tianzhou_agent_platform.conversations.attachments import (
    MAX_ATTACHMENT_BYTES,
    AttachmentPreview,
    MessageAttachment,
    content_disposition,
    too_large,
)
from tianzhou_agent_platform.conversations.models import Conversation, ConversationCreate, ConversationUpdate


def create_conversation_router() -> APIRouter:
    router = APIRouter()

    @router.post("/conversations", response_model=Conversation, status_code=status.HTTP_201_CREATED)
    async def create_conversation(payload: ConversationCreate, request: Request) -> Conversation:
        return await conversation_service(request).create(bind_actor(request, payload))

    @router.get("/conversations", response_model=list[Conversation])
    async def list_conversations(
        request: Request,
        user_id: str | None = None,
        tenant_id: str | None = None,
        category: str | None = None,
        workspace_id: str | None = None,
    ) -> list[Conversation]:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        return await conversation_service(request).list_for_actor(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            category=category,
            workspace_id=workspace_id,
        )

    @router.get("/conversations/{conversation_id}", response_model=Conversation)
    async def get_conversation(conversation_id: str, request: Request) -> Conversation:
        conversation = await conversation_service(request).get_reconciled(conversation_id)
        require_actor_ownership(request, user_id=conversation.user_id, tenant_id=conversation.tenant_id)
        return conversation

    @router.get("/conversations/{conversation_id}/aina-tasks", response_model=list[AinaTask])
    async def list_conversation_aina_tasks(conversation_id: str, request: Request) -> list[AinaTask]:
        """Background tasks that long-running AINA tools started in this conversation (design §7.3)."""
        conversation = await conversation_service(request).get(conversation_id)
        require_actor_ownership(request, user_id=conversation.user_id, tenant_id=conversation.tenant_id)
        return await aina_tasks(request).list_for_conversation(conversation_id)

    @router.post("/conversations/{conversation_id}/aina-tasks/{task_id}/cancel", response_model=AinaTask)
    async def cancel_conversation_aina_task(conversation_id: str, task_id: str, request: Request) -> AinaTask:
        """Stop a background task; the AINA learns it from its next callback or ``GET /tasks/{id}``."""
        conversation = await conversation_service(request).get(conversation_id)
        require_actor_ownership(request, user_id=conversation.user_id, tenant_id=conversation.tenant_id)
        return await aina_tasks(request).cancel(conversation_id, task_id)

    @router.post("/attachments", response_model=MessageAttachment, status_code=status.HTTP_201_CREATED)
    async def upload_attachment(
        request: Request,
        file: UploadFile = File(...),
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> MessageAttachment:
        """Keep a file the user picked for their next message, which names it by the returned file_ref.

        Only files Superbot can read are accepted (415 otherwise); one no message names within a day is deleted.
        """
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        payload = await read_upload(file)
        return await chat_attachments(request).upload(
            file.filename, payload, tenant_id=actor.tenant_id, user_id=actor.user_id
        )

    @router.delete("/attachments/{file_ref}", status_code=status.HTTP_204_NO_CONTENT)
    async def discard_attachment(
        file_ref: str, request: Request, user_id: str | None = None, tenant_id: str | None = None
    ) -> Response:
        """Delete an upload no message has named yet."""
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        await chat_attachments(request).discard(file_ref, tenant_id=actor.tenant_id, user_id=actor.user_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/attachments/{file_ref}/preview", response_model=AttachmentPreview)
    async def preview_attachment(
        file_ref: str, request: Request, user_id: str | None = None, tenant_id: str | None = None
    ) -> AttachmentPreview:
        """The start of the user's upload's text, sent or not."""
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        service = chat_attachments(request)
        attachment = await service.owned(file_ref, tenant_id=actor.tenant_id, user_id=actor.user_id)
        text, truncated = await service.preview(attachment)
        return AttachmentPreview(text=text, truncated=truncated)

    @router.get(
        "/attachments/{file_ref}",
        response_class=Response,
        responses={200: {"content": {"application/octet-stream": {}}, "description": "The file's bytes"}},
    )
    async def download_attachment(
        file_ref: str, request: Request, user_id: str | None = None, tenant_id: str | None = None
    ) -> Response:
        """The user's upload as a download, sent or not; not found once its file was deleted from the file tree."""
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        service = chat_attachments(request)
        attachment = await service.owned(file_ref, tenant_id=actor.tenant_id, user_id=actor.user_id)
        payload = await service.original(attachment)
        return Response(
            payload,
            media_type=attachment.content_type,
            headers={
                "Content-Disposition": content_disposition(attachment.name),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @router.patch("/conversations/{conversation_id}", response_model=Conversation)
    async def update_conversation(
        conversation_id: str,
        payload: ConversationUpdate,
        request: Request,
    ) -> Conversation:
        conversations = conversation_service(request)
        existing = await conversations.get(conversation_id)
        require_actor_ownership(request, user_id=existing.user_id, tenant_id=existing.tenant_id)
        return await conversations.update(conversation_id, payload)

    @router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_conversation(conversation_id: str, request: Request) -> Response:
        existing = await conversation_service(request).get(conversation_id)
        require_actor_ownership(request, user_id=existing.user_id, tenant_id=existing.tenant_id)
        await chat_service(request).delete_conversation(conversation_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/conversations/{conversation_id}/restore", response_model=Conversation)
    async def restore_conversation(conversation_id: str, request: Request) -> Conversation:
        conversations = conversation_service(request)
        existing = await conversations.get(conversation_id, include_deleted=True)
        require_actor_ownership(request, user_id=existing.user_id, tenant_id=existing.tenant_id)
        return await conversations.restore(conversation_id)

    return router


async def read_upload(file: UploadFile) -> bytes:
    try:
        payload = await file.read(MAX_ATTACHMENT_BYTES + 1)
    finally:
        await file.close()
    if len(payload) > MAX_ATTACHMENT_BYTES:
        raise too_large()
    return payload
