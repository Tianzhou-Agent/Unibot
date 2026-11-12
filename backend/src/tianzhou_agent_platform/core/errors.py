from __future__ import annotations

from tianzhou_agent_platform.core.base import StrictModel

# One error type for the platform and the agent core, which owns it.
from unibot_agent.errors import AgentError as PlatformError


class StandardError(StrictModel):
    code: str
    message: str
    retryable: bool
    source: str
    user_message: str
    trace_id: str


class ErrorEnvelope(StrictModel):
    error: StandardError


def not_found(resource: str, resource_id: str) -> PlatformError:
    return PlatformError(
        code="RESOURCE_NOT_FOUND",
        message=f"{resource} {resource_id!r} was not found",
        status_code=404,
        user_message=f"The requested {resource.lower()} does not exist.",
    )


def conflict(message: str) -> PlatformError:
    return PlatformError(code="CONFLICT", message=message, status_code=409)
