from typing import cast

from fastapi import Request

from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.core.agent import AgentRuntime
from tianzhou_agent_platform.core.repository import InMemoryRepository


def repository(request: Request) -> InMemoryRepository:
    return cast(InMemoryRepository, request.app.state.repository)


def runtime(request: Request) -> AgentRuntime:
    return cast(AgentRuntime, request.app.state.agent_runtime)


def gateway(request: Request) -> RemoteCapabilityGateway:
    return cast(RemoteCapabilityGateway, request.app.state.capability_gateway)


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
