"""Convert Unibot capabilities into native BaseTool adapters.

Domain operations stay in their feature services; this module only binds
capability metadata + invoke callbacks to LangChain tools for create_agent.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from typing import Any, Awaitable, Callable

from langchain_core.tools import StructuredTool

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
            user_message="参数无效，请修正后重试。",
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
            user_message="参数无效，请修正后重试。",
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


def capability_to_tool(
    *,
    function_name: str,
    description: str,
    input_schema: dict[str, Any],
    invoke: Callable[..., Awaitable[Any]],
    validate: bool = True,
) -> StructuredTool:
    """Bind one capability to a native tool that calls ``invoke``.

    ``validate=False`` leaves argument validation to ``invoke`` so the caller can record the failure.
    """

    async def _arun(**kwargs: Any) -> str:
        try:
            if validate:
                validate_capability_args(
                    input_schema=input_schema,
                    args=kwargs,
                    function_name=function_name,
                )
            outcome = await invoke(**kwargs)
        except Exception as exc:  # noqa: BLE001
            return format_error_envelope(exc)
        result = getattr(outcome, "result", outcome)
        if isinstance(result, str):
            return result
        return json.dumps(result, ensure_ascii=False, default=str)

    def _run(**kwargs: Any) -> str:
        # Sync fallback: schedule the coroutine on the running loop when possible.
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return json.dumps({"error": "async tool invoked without a running loop"}, ensure_ascii=False)
        return loop.run_until_complete(_arun(**kwargs)) if not loop.is_running() else ""

    _arun.__name__ = function_name
    _arun.__doc__ = description
    _run.__name__ = function_name
    _run.__doc__ = description
    # The capability's own JSON Schema is advertised verbatim and arguments reach ``invoke`` exactly as the
    # model sent them; a generated pydantic model would inject None for omitted optional fields, which the
    # capability schema then rejects.
    return StructuredTool.from_function(
        _arun,
        coroutine=_arun,
        name=function_name,
        description=description,
        args_schema=input_schema or {"type": "object", "properties": {}},
    )
