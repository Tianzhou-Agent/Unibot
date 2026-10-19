import hashlib

from fastapi import APIRouter, File, Request, Response, UploadFile, status

from tianzhou_agent_platform.aina.package.tasks import AinaTask
from tianzhou_agent_platform.api.dependencies import (
    actor_scope,
    aina_file_blobs,
    aina_tasks,
    bind_actor,
    chat_service,
    conversation_service,
    require_actor_ownership,
)
from tianzhou_agent_platform.conversations.attachments import (
    DEFAULT_TYPE,
    MAX_ATTACHMENT_BYTES,
    Attachment,
    MessageAttachment,
    blob_key,
    clean_name,
    content_disposition,
    new_file_ref,
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

    @router.post(
        "/conversations/{conversation_id}/attachments",
        response_model=MessageAttachment,
        status_code=status.HTTP_201_CREATED,
    )
    async def upload_attachment(conversation_id: str, request: Request, file: UploadFile = File(...)) -> MessageAttachment:
        """Store a file for the next message of this conversation; the message names it by the returned file_ref."""
        conversation = await conversation_service(request).get(conversation_id)
        require_actor_ownership(request, user_id=conversation.user_id, tenant_id=conversation.tenant_id)
        try:
            payload = await file.read(MAX_ATTACHMENT_BYTES + 1)
        finally:
            await file.close()
        if len(payload) > MAX_ATTACHMENT_BYTES:
            raise too_large()
        content_type = (file.content_type or DEFAULT_TYPE).split(";")[0].strip()[:160] or DEFAULT_TYPE
        attachment = Attachment(
            id=new_file_ref(),
            tenant_id=conversation.tenant_id,
            user_id=conversation.user_id,
            conversation_id=conversation.id,
            name=clean_name(file.filename),
            content_type=content_type,
            size_bytes=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        # The bytes first: a message can name the file only once they are stored.
        await aina_file_blobs(request).write(blob_key(attachment), payload)
        return await conversation_service(request).add_attachment(attachment)

    @router.get(
        "/conversations/{conversation_id}/attachments/{file_ref}",
        response_class=Response,
        responses={200: {"content": {"application/octet-stream": {}}, "description": "The file's bytes"}},
    )
    async def download_attachment(conversation_id: str, file_ref: str, request: Request) -> Response:
        conversations = conversation_service(request)
        conversation = await conversations.get(conversation_id)
        require_actor_ownership(request, user_id=conversation.user_id, tenant_id=conversation.tenant_id)
        attachment = await conversations.attachment(conversation, file_ref)
        payload = await aina_file_blobs(request).read(blob_key(attachment))
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
