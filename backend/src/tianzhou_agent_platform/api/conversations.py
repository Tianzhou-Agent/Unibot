from fastapi import APIRouter, Request, Response, status

from tianzhou_agent_platform.api.dependencies import actor_scope, bind_actor, repository, require_actor_ownership
from tianzhou_agent_platform.core.conversation import Conversation, ConversationCreate, ConversationUpdate


def create_conversation_router() -> APIRouter:
    router = APIRouter()

    @router.post("/conversations", response_model=Conversation, status_code=status.HTTP_201_CREATED)
    async def create_conversation(payload: ConversationCreate, request: Request) -> Conversation:
        return await repository(request).create_conversation(bind_actor(request, payload))

    @router.get("/conversations", response_model=list[Conversation])
    async def list_conversations(
        request: Request,
        user_id: str | None = None,
        tenant_id: str | None = None,
        category: str | None = None,
        workspace_id: str | None = None,
    ) -> list[Conversation]:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        data_repository = repository(request)
        if workspace_id is not None:
            await data_repository.require_workspace_actor(
                workspace_id,
                user_id=actor.user_id,
                tenant_id=actor.tenant_id,
            )
        return await data_repository.list_conversations(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            category=category,
            workspace_id=workspace_id,
        )

    @router.get("/conversations/{conversation_id}", response_model=Conversation)
    async def get_conversation(conversation_id: str, request: Request) -> Conversation:
        conversation = await repository(request).reconcile_conversation_run(conversation_id)
        require_actor_ownership(request, user_id=conversation.user_id, tenant_id=conversation.tenant_id)
        return conversation

    @router.patch("/conversations/{conversation_id}", response_model=Conversation)
    async def update_conversation(
        conversation_id: str,
        payload: ConversationUpdate,
        request: Request,
    ) -> Conversation:
        existing = await repository(request).get_conversation(conversation_id)
        require_actor_ownership(request, user_id=existing.user_id, tenant_id=existing.tenant_id)
        return await repository(request).update_conversation(conversation_id, payload)

    @router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_conversation(conversation_id: str, request: Request) -> Response:
        existing = await repository(request).get_conversation(conversation_id)
        require_actor_ownership(request, user_id=existing.user_id, tenant_id=existing.tenant_id)
        await repository(request).set_conversation_status(conversation_id, "deleted")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/conversations/{conversation_id}/restore", response_model=Conversation)
    async def restore_conversation(conversation_id: str, request: Request) -> Conversation:
        existing = await repository(request).get_conversation(conversation_id, include_deleted=True)
        require_actor_ownership(request, user_id=existing.user_id, tenant_id=existing.tenant_id)
        return await repository(request).set_conversation_status(conversation_id, "active")

    return router
