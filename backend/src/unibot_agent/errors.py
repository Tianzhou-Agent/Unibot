"""The error the agent core raises and understands."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class AgentError(Exception):
    code: str
    message: str
    # Severity in HTTP terms, for hosts that serve the agent over HTTP.
    status_code: int = 400
    retryable: bool = False
    source: str = "platform"
    user_message: str | None = None
    debug: dict[str, Any] | None = None

    def __str__(self) -> str:
        return self.message


def exception_detail(exc: BaseException) -> str:
    detail = str(exc).strip() or type(exc).__name__
    cause = exc.__cause__ or exc.__context__
    if cause is None:
        return detail
    cause_text = f"{type(cause).__name__}: {cause}"
    if cause_text in detail:
        return detail
    return f"{detail} ({cause_text})"
