"""MCP client of the Plugin Host: ``tools/list`` at registration and ``tools/call`` per invocation (design §6.1, §6.4).

Remote servers are reached over Streamable HTTP with the official MCP SDK. ``servers`` maps an endpoint URL to an
in-process server instead, which bundled first-party AINAs (design §11) and tests use; the calls are identical.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp_types import RequestParamsMeta
from pydantic import Field

from tianzhou_agent_platform.aina.package.models import McpToolDescriptor
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.errors import PlatformError


class McpServerInfo(StrictModel):
    name: str | None = None
    version: str | None = None
    tools: list[McpToolDescriptor] = Field(default_factory=list)


class McpCallResult(StrictModel):
    content: list[dict[str, Any]] = Field(default_factory=list)
    structured_content: dict[str, Any] | None = None
    is_error: bool = False
    meta: dict[str, Any] = Field(default_factory=dict)


class McpToolClient:
    def __init__(self, *, servers: Mapping[str, Any] | None = None) -> None:
        self.servers = dict(servers or {})

    async def list_tools(self, url: str, *, headers: Mapping[str, str] | None = None, timeout: float = 30.0) -> McpServerInfo:
        try:
            async with self._connect(url, headers=headers, timeout=timeout) as client:
                tools: list[McpToolDescriptor] = []
                cursor: str | None = None
                while True:
                    page = await client.list_tools(cursor=cursor, cache_mode="bypass")
                    tools.extend(
                        McpToolDescriptor(
                            name=tool.name,
                            title=tool.title,
                            description=tool.description or "",
                            input_schema=tool.input_schema,
                            output_schema=tool.output_schema,
                            annotations=(
                                tool.annotations.model_dump(mode="json", by_alias=True, exclude_none=True)
                                if tool.annotations is not None
                                else {}
                            ),
                        )
                        for tool in page.tools
                    )
                    cursor = page.next_cursor
                    if cursor is None:
                        break
                info = client.server_info
                return McpServerInfo(
                    name=info.name if info is not None else None,
                    version=(info.version or None) if info is not None else None,
                    tools=tools,
                )
        except PlatformError:
            raise
        except Exception as exc:
            raise _failure(exc, "The MCP server could not list its tools") from exc

    async def call_tool(
        self,
        url: str,
        name: str,
        arguments: dict[str, Any],
        *,
        meta: dict[str, Any],
        headers: Mapping[str, str] | None = None,
        timeout: float = 30.0,
    ) -> McpCallResult:
        try:
            async with self._connect(url, headers=headers, timeout=timeout) as client:
                result = await client.call_tool(name, arguments, read_timeout_seconds=timeout, meta=cast(RequestParamsMeta, meta))
        except PlatformError:
            raise
        except Exception as exc:
            raise _failure(exc, f"MCP tool {name!r} could not be called") from exc
        return McpCallResult(
            content=[item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in result.content],
            structured_content=result.structured_content,
            is_error=bool(result.is_error),
            meta=dict(result.meta or {}),
        )

    @asynccontextmanager
    async def _connect(
        self,
        url: str,
        *,
        headers: Mapping[str, str] | None,
        timeout: float,
    ) -> AsyncIterator[Client]:
        server = self.servers.get(url)
        if server is not None:
            async with Client(server, raise_exceptions=True, read_timeout_seconds=timeout) as client:
                yield client
            return
        async with httpx2.AsyncClient(
            headers=dict(headers or {}),
            timeout=httpx2.Timeout(timeout),
            follow_redirects=False,
        ) as http_client:
            transport = streamable_http_client(url, http_client=http_client)
            async with Client(transport, raise_exceptions=True, read_timeout_seconds=timeout) as client:
                yield client


def _failure(exc: BaseException, message: str) -> PlatformError:
    if isinstance(exc, TimeoutError) or any(isinstance(item, TimeoutError) for item in _leaves(exc)):
        return PlatformError("TIMEOUT", message, status_code=504, retryable=True, source="mcp")
    status = next(
        (
            item.response.status_code
            for item in _leaves(exc)
            if isinstance(item, httpx2.HTTPStatusError)
        ),
        None,
    )
    if status in {401, 403}:
        return PlatformError("AUTHENTICATION_FAILED", message, status_code=502, source="mcp")
    return PlatformError(
        "DEPENDENCY_FAILED",
        message,
        status_code=502,
        retryable=status is None or status >= 500,
        source="mcp",
        debug={"error": type(exc).__name__, **({"remote_status": status} if status is not None else {})},
    )


def _leaves(exc: BaseException) -> list[BaseException]:
    """The exception and everything it groups or wraps (anyio task groups raise ``ExceptionGroup``)."""
    found: list[BaseException] = [exc]
    if isinstance(exc, BaseExceptionGroup):
        for item in exc.exceptions:
            found.extend(_leaves(item))
    if exc.__cause__ is not None and exc.__cause__ is not exc:
        found.extend(_leaves(exc.__cause__))
    return found
