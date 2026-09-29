"""Automatic tool retries for Unibot (plan §6.7).

Automatic retries use native ``ToolRetryMiddleware``: only side-effect-free remote tools are retried, each with its
own configured ``retries`` budget, and only for transient failures. The remote gateway makes a single attempt, so
retries are never nested. Side-effecting tools and AINA operations are never retried automatically.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Sequence, cast

from langchain.agents.middleware import ToolRetryMiddleware

from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.services.agent_integration.capabilities import Capability


def is_transient_error(exc: BaseException) -> bool:
    """Timeouts, unreachable hosts and remote 5xx responses; never rate limits or invalid payloads."""
    if isinstance(exc, PlatformError):
        return exc.retryable and (exc.debug or {}).get("remote_status") != 429
    return isinstance(exc, (TimeoutError, ConnectionError))


class _ToolRetryGroup(ToolRetryMiddleware):
    """``ToolRetryMiddleware`` for the tools sharing one retry budget (instances need distinct names)."""

    def __init__(self, *, max_retries: int, tools: Sequence[str]) -> None:
        super().__init__(
            max_retries=max_retries,
            tools=list(tools),
            retry_on=is_transient_error,
            on_failure="error",
            backoff_factor=2.0,
            initial_delay=0.1,
            max_delay=5.0,
            jitter=False,
        )
        self._label = f"{type(self).__name__}[{max_retries}]"

    @property
    def name(self) -> str:
        return self._label


def tool_retry_middleware(registry: dict[str, Capability]) -> list[ToolRetryMiddleware]:
    """One retry middleware per distinct retry budget of the registered side-effect-free remote tools.

    ``on_failure="error"`` re-raises the final failure, so compose these innermost, inside the capability-call
    wrapper that records one span per logical call and turns the failure into the model-visible error.
    """
    groups: dict[int, list[str]] = defaultdict(list)
    for name, capability in registry.items():
        if capability.retries_transiently:
            retries = cast(ToolRecord, capability.value).retries
            if retries > 0:
                groups[retries].append(name)
    return [_ToolRetryGroup(max_retries=retries, tools=names) for retries, names in sorted(groups.items())]
