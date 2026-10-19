from fastapi import APIRouter

from tianzhou_agent_platform.api.aina_add_app import create_aina_add_app_router
from tianzhou_agent_platform.api.aina_canvas import create_aina_canvas_router
from tianzhou_agent_platform.api.aina_host import create_aina_host_router
from tianzhou_agent_platform.api.aina_host_services import create_aina_host_services_router
from tianzhou_agent_platform.api.aina_oauth import create_aina_oauth_router
from tianzhou_agent_platform.api.aina_ui import create_aina_ui_router
from tianzhou_agent_platform.api.aina_projects import create_aina_project_router
from tianzhou_agent_platform.api.auth import create_auth_router
from tianzhou_agent_platform.api.capabilities import create_capability_router
from tianzhou_agent_platform.api.chat import create_chat_router
from tianzhou_agent_platform.api.conversations import create_conversation_router
from tianzhou_agent_platform.api.developer_tokens import create_developer_token_router
from tianzhou_agent_platform.api.notifications import create_notification_router
from tianzhou_agent_platform.api.documents import create_document_router
from tianzhou_agent_platform.api.feedback import create_feedback_router
from tianzhou_agent_platform.api.document_tasks import create_document_task_router
from tianzhou_agent_platform.api.memories import create_memory_router
from tianzhou_agent_platform.api.model_settings import create_model_settings_router
from tianzhou_agent_platform.api.operations import create_operations_router
from tianzhou_agent_platform.api.schedules import create_schedule_router
from tianzhou_agent_platform.api.sandboxes import create_sandbox_router
from tianzhou_agent_platform.api.workspaces import create_workspace_router
from tianzhou_agent_platform.api.tasks import create_task_router


def create_router() -> APIRouter:
    router = APIRouter()
    router.include_router(create_auth_router())
    router.include_router(create_developer_token_router())
    router.include_router(create_notification_router())
    router.include_router(create_operations_router())
    router.include_router(create_chat_router())
    router.include_router(create_workspace_router())
    router.include_router(create_conversation_router())
    router.include_router(create_document_task_router())
    router.include_router(create_document_router())
    router.include_router(create_feedback_router())
    router.include_router(create_memory_router())
    router.include_router(create_model_settings_router())
    router.include_router(create_aina_project_router())
    router.include_router(create_aina_add_app_router())
    router.include_router(create_capability_router())
    router.include_router(create_aina_host_router())
    router.include_router(create_aina_host_services_router())
    router.include_router(create_aina_ui_router())
    router.include_router(create_aina_oauth_router())
    router.include_router(create_aina_canvas_router())
    router.include_router(create_schedule_router())
    router.include_router(create_sandbox_router())
    router.include_router(create_task_router())
    return router
