"""Provider (OpenAI-compatible) exceptions mapped to platform errors, shared by every model caller."""

from __future__ import annotations

import openai

from tianzhou_agent_platform.core.errors import PlatformError


def exception_detail(exc: BaseException) -> str:
    detail = str(exc).strip() or type(exc).__name__
    cause = exc.__cause__ or exc.__context__
    if cause is None:
        return detail
    cause_text = f"{type(cause).__name__}: {cause}"
    if cause_text in detail:
        return detail
    return f"{detail} ({cause_text})"


def map_provider_error(exc: openai.OpenAIError) -> PlatformError:
    if isinstance(exc, openai.APITimeoutError):
        return PlatformError(
            "TIMEOUT",
            "The model request timed out",
            status_code=504,
            retryable=True,
            source="model",
        )
    if isinstance(exc, openai.APIConnectionError):
        return PlatformError(
            "DEPENDENCY_FAILED",
            "The model provider could not be reached",
            status_code=502,
            retryable=True,
            source="model",
            debug={"provider_error": exception_detail(exc)},
        )
    status_code = getattr(exc, "status_code", None)
    return PlatformError(
        "DEPENDENCY_FAILED",
        f"The model provider returned HTTP {status_code}" if status_code else "The model request failed",
        status_code=502,
        retryable=status_code is not None and (status_code >= 500 or status_code == 429),
        source="model",
        debug={"provider_status": status_code} if status_code is not None else {},
    )
