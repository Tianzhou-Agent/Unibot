from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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
