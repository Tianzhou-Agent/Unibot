"""Capability tool-call helpers: argument validation, handled failures and model-visible error envelopes."""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any

# The model's id for the tool call being executed; set by CapabilityScopeMiddleware around each tool call so
# executors can record the same logical call id the model and the transcript use.
current_tool_call_id: ContextVar[str | None] = ContextVar("current_tool_call_id", default=None)


def validate_capability_args(
    *,
    input_schema: dict[str, Any],
    args: Any,
    function_name: str,
) -> None:
    """Validate args against the capability JSON Schema before approval/execution."""
    from tianzhou_agent_platform.core.errors import PlatformError

    if not isinstance(args, dict):
        raise PlatformError(
            code="INVALID_REQUEST",
            message=f"Capability {function_name} arguments must be an object",
            status_code=400,
            source="tool",
            user_message="Invalid arguments; fix them and retry.",
        )
    try:
        from tianzhou_agent_platform.core.schema import validate_value

        validate_value(args, input_schema, label=f"Capability {function_name} arguments")
    except Exception as exc:  # noqa: BLE001
        if isinstance(exc, PlatformError):
            raise
        raise PlatformError(
            code="INVALID_REQUEST",
            message=f"Capability {function_name} arguments failed schema validation",
            status_code=400,
            source="tool",
            user_message="Invalid arguments; fix them and retry.",
        ) from exc


class CapabilityToolError(Exception):
    """A handled capability failure whose model-visible payload is already built (error, instruction, recovery)."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(str(payload.get("error", {}).get("message") or "capability failed"))
        self.payload = payload


def format_error_envelope(exc: Exception) -> str:
    from tianzhou_agent_platform.core.errors import PlatformError

    if isinstance(exc, CapabilityToolError):
        return json.dumps(exc.payload, ensure_ascii=False)
    if isinstance(exc, PlatformError):
        return json.dumps(
            {
                "error": {
                    "code": exc.code,
                    "message": exc.user_message or exc.message,
                    "retryable": exc.retryable,
                    "source": exc.source,
                }
            },
            ensure_ascii=False,
        )
    return json.dumps(
        {
            "error": {
                "code": "DEPENDENCY_FAILED",
                "message": str(exc) or type(exc).__name__,
                "retryable": False,
                "source": "tool",
            }
        },
        ensure_ascii=False,
    )
