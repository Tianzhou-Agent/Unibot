"""Observation domain events at explicit application boundaries.

Repository/application events replace ObservedAgentRepository proxying.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ObservationService(Protocol):
    async def on_model_start(self, **kwargs: Any) -> None: ...

    async def on_model_end(self, **kwargs: Any) -> None: ...

    async def on_model_error(self, **kwargs: Any) -> None: ...

    async def on_tool_start(self, **kwargs: Any) -> None: ...

    async def on_tool_end(self, **kwargs: Any) -> None: ...

    async def on_tool_error(self, **kwargs: Any) -> None: ...

    async def on_token(self, **kwargs: Any) -> None: ...

    async def on_retry(self, **kwargs: Any) -> None: ...


async def emit_repository_event(
    sink: Any | None,
    *,
    kind: str,
    status: str,
    target_type: str | None = None,
    target_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Explicit repository/application event (approval create, archive append…)."""
    if sink is None:
        return
    handler = getattr(sink, "on_repository_event", None)
    if handler is None:
        return
    await handler(
        kind=kind,
        status=status,
        target_type=target_type,
        target_id=target_id,
        details=details or {},
    )
