"""Retry eligibility policy for Unibot (plan §6.7).

Only explicitly eligible read-only tools get automatic retries. Side-effecting
tools and uncertain AINA operations are excluded. Physical attempt ceilings
and idempotency keys are preserved through native ToolRetryMiddleware config.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from langchain.agents.middleware import ModelCallLimitMiddleware, ToolRetryMiddleware


def is_transient_error(exc: BaseException) -> bool:
    """Transient-error predicate for eligible tool retries."""
    from tianzhou_agent_platform.core.errors import PlatformError

    if isinstance(exc, PlatformError):
        return bool(exc.retryable)
    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return True
    name = type(exc).__name__
    return name in {"Timeout", "ReadTimeout", "ConnectTimeout", "RemoteProtocolError"}


def build_tool_retry_middleware(
    *,
    eligible_tools: Sequence[str],
    max_retries: int = 2,
    on_failure: str = "error",
    backoff_factor: float = 0.5,
    initial_delay: float = 0.1,
    max_delay: float = 5.0,
) -> ToolRetryMiddleware:
    """Native retries only for explicitly eligible read-only tools.

    ``on_failure="error"`` routes exhausted retries to ToolErrorMiddleware
    (compose ToolRetryMiddleware *inner* relative to ToolErrorMiddleware).
    """
    return ToolRetryMiddleware(
        max_retries=max_retries,
        tools=list(eligible_tools),
        retry_on=is_transient_error,
        on_failure=on_failure,  # type: ignore[arg-type]
        backoff_factor=backoff_factor,
        initial_delay=initial_delay,
        max_delay=max_delay,
        jitter=False,
    )


def build_model_call_limit(
    *,
    run_limit: int | None = None,
    thread_limit: int | None = None,
    exit_behavior: str = "end",
) -> ModelCallLimitMiddleware:
    """Native model-call budget. A pause must not grant unlimited extra calls.

    ``thread_limit`` persists across runs on the same thread (survives approval
    pause). ``run_limit`` is per invocation. Public reporting derives deltas
    from native counters.
    """
    return ModelCallLimitMiddleware(
        run_limit=run_limit,
        thread_limit=thread_limit,
        exit_behavior=exit_behavior,  # type: ignore[arg-type]
    )


def side_effecting_tool_names(names: Iterable[str]) -> set[str]:
    """Explicit denylist helper — names that must never auto-retry."""
    return set(names)
