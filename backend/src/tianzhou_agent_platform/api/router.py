from fastapi import APIRouter

from tianzhou_agent_platform.api.capabilities import create_capability_router
from tianzhou_agent_platform.api.chat import create_chat_router
from tianzhou_agent_platform.api.conversations import create_conversation_router
from tianzhou_agent_platform.api.documents import create_document_router
from tianzhou_agent_platform.api.memories import create_memory_router
from tianzhou_agent_platform.api.model_settings import create_model_settings_router
from tianzhou_agent_platform.api.operations import create_operations_router


def create_router() -> APIRouter:
    router = APIRouter()
    router.include_router(create_operations_router())
    router.include_router(create_chat_router())
    router.include_router(create_conversation_router())
    router.include_router(create_document_router())
    router.include_router(create_memory_router())
    router.include_router(create_model_settings_router())
    router.include_router(create_capability_router())
    return router
