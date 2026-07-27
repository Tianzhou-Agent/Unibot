from typing import cast

from fastapi import Request
from pydantic import Field

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
