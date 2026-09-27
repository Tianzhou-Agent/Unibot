"""Convert Unibot capabilities into native BaseTool adapters.

Domain operations stay in their feature services; this module only binds
capability metadata + invoke callbacks to LangChain tools for create_agent.
"""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, ConfigDict, create_model


class _OpenArgs(BaseModel):
    model_config = ConfigDict(extra="allow")


def _args_schema(input_schema: dict[str, Any] | None) -> type[BaseModel]:
    props = (input_schema or {}).get("properties") or {}
    fields: dict[str, Any] = {}
    # Accept every declared property as optional Any so ToolNode does not strip
    # extras; product validation happens in validate_capability_args.
    for name in props:
        fields[name] = (Any, None)
    if not fields:
        return _OpenArgs
    return create_model("CapabilityArgs", **fields)  # type: ignore[call-overload]


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


def format_error_envelope(exc: Exception) -> str:
    from tianzhou_agent_platform.core.errors import PlatformError

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
) -> StructuredTool:
    """Bind one capability to a native tool that calls ``invoke``."""

    async def _arun(**kwargs: Any) -> str:
        try:
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
    return StructuredTool.from_function(
        _arun,
        coroutine=_arun,
        name=function_name,
        description=description,
        args_schema=_args_schema(input_schema),
    )
