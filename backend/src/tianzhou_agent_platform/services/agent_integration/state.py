"""Application scope/widget state and invocation context.

Extends core.agent_runtime.state with AINA scope and widget fields.
"""

from __future__ import annotations

from typing import Any, TypedDict

from tianzhou_agent_platform.core.agent_runtime.state import RuntimeAgentState


class ApplicationContext(TypedDict, total=False):
    """Invocation context: services, credentials, UI context and model config.

    Not serialized into checkpoints.
    """

    services: dict[str, Any]
    credentials: dict[str, Any]
    ui_context: str | None
    model_config: dict[str, Any]


class ApplicationAgentState(RuntimeAgentState, total=False):
    """Working state extensions for active AINA scope and widgets."""

    active_aina_id: str | None
    active_scope: dict[str, Any] | None
    widgets: list[dict[str, Any]]
