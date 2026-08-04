from typing import cast

from fastapi import Request
from pydantic import BaseModel, Field

from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.aina.document.task_service import DocumentEditTaskService
from tianzhou_agent_platform.aina.project_service import AinaProjectService
from tianzhou_agent_platform.aina.managed import ManagedAinaRuntime
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.agent import AgentRuntime
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.sandbox.service import SandboxService
from tianzhou_agent_platform.vision.client import VisionClient
from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.auth.service import AuthService


class RequestActor(StrictModel):
    """Actor resolved by trusted ASGI middleware, never by request parameters."""

    user_id: str = Field(min_length=1, max_length=160)
    tenant_id: str = Field(min_length=1, max_length=160)


_LOCAL_ACTOR = RequestActor(user_id="anonymous", tenant_id="default")


def request_actor(request: Request) -> RequestActor:
    actor = getattr(request.state, "actor", None)
    if actor is None:
        return _LOCAL_ACTOR
    if isinstance(actor, RequestActor):
        return actor
    return RequestActor.model_validate(actor)


def actor_scope(
    request: Request,
    *,
    user_id: str | None = None,
    tenant_id: str | None = None,
) -> RequestActor:
    actor = getattr(request.state, "actor", None)
    if actor is not None:
        return request_actor(request)
    return RequestActor(user_id=user_id or _LOCAL_ACTOR.user_id, tenant_id=tenant_id or _LOCAL_ACTOR.tenant_id)


def bind_actor[ModelT: BaseModel](request: Request, payload: ModelT) -> ModelT:
    actor = getattr(request.state, "actor", None)
    if actor is None:
        return payload
    resolved = request_actor(request)
    fields = type(payload).model_fields
    updates = {
        name: value
        for name, value in {"user_id": resolved.user_id, "tenant_id": resolved.tenant_id}.items()
        if name in fields
    }
    return payload.model_copy(update=updates, deep=True)


def require_actor_ownership(request: Request, *, user_id: str, tenant_id: str) -> None:
    if getattr(request.state, "actor", None) is None:
        return
    actor = request_actor(request)
    if actor.user_id != user_id or actor.tenant_id != tenant_id:
        from tianzhou_agent_platform.core.errors import PlatformError

        raise PlatformError(
            "PERMISSION_DENIED",
            "Resource ownership does not match the authenticated user",
            status_code=403,
            source="auth",
            user_message="无权访问该资源。",
        )


def repository(request: Request) -> InMemoryRepository:
    return cast(InMemoryRepository, request.app.state.repository)


def settings(request: Request) -> AgentSettings:
    return cast(AgentSettings, request.app.state.settings)


def runtime(request: Request) -> AgentRuntime:
    return cast(AgentRuntime, request.app.state.agent_runtime)


def gateway(request: Request) -> RemoteCapabilityGateway:
    return cast(RemoteCapabilityGateway, request.app.state.capability_gateway)


def aina_projects(request: Request) -> AinaProjectService:
    return cast(AinaProjectService, request.app.state.aina_project_service)


def managed_ainas(request: Request) -> ManagedAinaRuntime:
    return cast(ManagedAinaRuntime, request.app.state.managed_aina_runtime)


def sandboxes(request: Request) -> SandboxService:
    return cast(SandboxService, request.app.state.sandbox_service)


def vision(request: Request) -> VisionClient:
    return cast(VisionClient, request.app.state.vision_client)


def auth(request: Request) -> AuthService:
    return cast(AuthService, request.app.state.auth_service)


def current_user(request: Request) -> UserRecord:
    user = getattr(request.state, "user", None)
    if not isinstance(user, UserRecord):
        from tianzhou_agent_platform.core.errors import PlatformError

        raise PlatformError(
            "AUTHENTICATION_REQUIRED",
            "Authentication is required",
            status_code=401,
            source="auth",
            user_message="请先登录。",
        )
    return user


def documents(request: Request) -> DocumentService:
    service = cast(DocumentService | None, request.app.state.document_service)
    if service is None:
        from tianzhou_agent_platform.core.errors import PlatformError

        raise PlatformError(
            "DEPENDENCY_FAILED",
            "Document NAS storage is unavailable",
            status_code=503,
            source="storage",
        )
    return service


def document_edit_tasks(request: Request) -> DocumentEditTaskService:
    service = cast(DocumentEditTaskService | None, request.app.state.document_edit_task_service)
    if service is None:
        from tianzhou_agent_platform.core.errors import PlatformError

        raise PlatformError(
            "DEPENDENCY_FAILED",
            "Document edit tasks are unavailable",
            status_code=503,
            source="storage",
        )
    return service
