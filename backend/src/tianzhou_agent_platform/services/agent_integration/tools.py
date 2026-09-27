"""Native tool schemas/adapters and product error formatting.

ToolErrorMiddleware formatter lives here. Domain services are invoked through
ToolRuntime context; this module does not reimplement domain operations.
"""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.tools import BaseTool, tool
from langgraph.prebuilt import ToolRuntime


def format_tool_error(exc: Exception, request: Any) -> str | None:
    """Map platform/tool exceptions to model-visible error ToolMessage content.

    Returning None lets the exception propagate (halting the run).
    Prefer exception type over raw message to avoid leaking internals.
    """
    from tianzhou_agent_platform.core.errors import PlatformError

    if isinstance(exc, PlatformError):
        return f"{exc.code}: {exc.user_message or exc.message}"
    if isinstance(exc, (ValueError, TypeError, KeyError)):
        return f"Invalid arguments for {getattr(request, 'tool_call', {}).get('name', 'tool')}: fix the input and retry."
    return None


def bind_echo_tool() -> BaseTool:
    """Example fixed tool used by one-tool native scenarios."""

    @tool
    def echo_tool(text: str, runtime: ToolRuntime) -> str:
        """Echo the provided text back to the caller."""
        return f"echo:{text}"

    return echo_tool
