from fastapi import APIRouter, Request, Response, status

from tianzhou_agent_platform.api.dependencies import repository
from tianzhou_agent_platform.core.conversation import Conversation, ConversationCreate, ConversationUpdate


def create_conversation_router() -> APIRouter:
    router = APIRouter()

    @router.post("/conversations", response_model=Conversation, status_code=status.HTTP_201_CREATED)
    async def create_conversation(payload: ConversationCreate, request: Request) -> Conversation:
        return await repository(request).create_conversation(payload)

    @router.get("/conversations", response_model=list[Conversation])
    async def list_conversations(
        request: Request,
        user_id: str | None = None,
        tenant_id: str | None = None,
        category: str | None = None,
    ) -> list[Conversation]:
        return await repository(request).list_conversations(
            user_id=user_id,
            tenant_id=tenant_id,
            category=category,
        )

    @router.get("/conversations/{conversation_id}", response_model=Conversation)
    async def get_conversation(conversation_id: str, request: Request) -> Conversation:
        return await repository(request).get_conversation(conversation_id)

    @router.patch("/conversations/{conversation_id}", response_model=Conversation)
    async def update_conversation(
        conversation_id: str,
        payload: ConversationUpdate,
        request: Request,
    ) -> Conversation:
        return await repository(request).update_conversation(conversation_id, payload)

    @router.delete("/conversations/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_conversation(conversation_id: str, request: Request) -> Response:
        await repository(request).set_conversation_status(conversation_id, "deleted")
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/conversations/{conversation_id}/restore", response_model=Conversation)
    async def restore_conversation(conversation_id: str, request: Request) -> Conversation:
        return await repository(request).set_conversation_status(conversation_id, "active")

    return router
