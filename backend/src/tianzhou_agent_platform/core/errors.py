from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeVar

_ServiceT = TypeVar("_ServiceT")


@dataclass(slots=True)
class PlatformError(Exception):
    code: str
    message: str
    status_code: int = 400
    retryable: bool = False
    source: str = "platform"
    user_message: str | None = None
    debug: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


def not_found(resource: str, resource_id: str) -> PlatformError:
    return PlatformError(
        code="RESOURCE_NOT_FOUND",
        message=f"{resource} {resource_id!r} was not found",
        status_code=404,
        user_message=f"The requested {resource.lower()} does not exist.",
    )


def conflict(message: str) -> PlatformError:
    return PlatformError(code="CONFLICT", message=message, status_code=409)


def unknown_tool_error(tool_id: str, *, kind: str = "tool") -> PlatformError:
    return PlatformError(
        "RESOURCE_NOT_FOUND",
        f"Unknown {kind} tool {tool_id!r}",
        status_code=404,
    )


def require_service(value: _ServiceT | None, *, message: str, source: str = "platform") -> _ServiceT:
    """Return the service, or raise a 503 when it is not available."""
    if value is None:
        raise PlatformError("DEPENDENCY_FAILED", message, status_code=503, source=source)
    return value
