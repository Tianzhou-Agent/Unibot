from __future__ import annotations

from contextlib import asynccontextmanager
from time import perf_counter
from typing import Any, AsyncIterator

import httpx2
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client

from tianzhou_agent_platform.aina.mcp.models import McpServerCreate, McpServerRecord, McpToolDescriptor
from tianzhou_agent_platform.aina.security.models import Authentication
from tianzhou_agent_platform.core.errors import PlatformError


class McpCapabilityClient:
    """Official MCP SDK adapter for Streamable HTTP servers."""

    async def discover(self, server: McpServerCreate) -> McpServerRecord:
        try:
            async with self._connect(server) as client:
                tools: list[McpToolDescriptor] = []
                cursor: str | None = None
                while True:
                    page = await client.list_tools(cursor=cursor)
                    tools.extend(
                        McpToolDescriptor(
                            name=tool.name,
                            title=tool.title,
                            description=tool.description or tool.title or "MCP tool",
                            input_schema=tool.input_schema,
                            output_schema=tool.output_schema or {},
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
                server_info = client.server_info
                values = {name: getattr(server, name) for name in McpServerCreate.model_fields}
                return McpServerRecord(
                    **values,
                    protocol_version=client.protocol_version,
                    server_name=server_info.name if server_info is not None else None,
                    server_version=server_info.version if server_info is not None else None,
                    instructions=client.instructions,
                    tools=tools,
                )
        except PlatformError:
            raise
        except Exception as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "The MCP server could not be discovered",
                status_code=502,
                retryable=True,
                source="mcp",
            ) from exc

    async def call_tool(
        self,
        server: McpServerRecord,
        tool: McpToolDescriptor,
        arguments: dict[str, Any],
    ) -> tuple[Any, float]:
        started = perf_counter()
        try:
            async with self._connect(server) as client:
                result = await client.call_tool(
                    tool.name,
                    arguments,
                    read_timeout_seconds=server.timeout_seconds,
                )
        except PlatformError:
            raise
        except Exception as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                f"MCP tool {tool.name!r} could not be called",
                status_code=502,
                retryable=True,
                source="mcp",
            ) from exc
        if result.is_error:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                f"MCP tool {tool.name!r} returned an error",
                status_code=502,
                source="mcp",
                debug={"content": _content_payload(result.content)},
            )
        payload: Any
        if result.structured_content is not None:
            payload = result.structured_content
        else:
            payload = {"content": _content_payload(result.content)}
        return payload, (perf_counter() - started) * 1000

    @asynccontextmanager
    async def _connect(self, server: McpServerCreate) -> AsyncIterator[Client]:
        headers = _authentication_headers(server.authentication)
        timeout = httpx2.Timeout(server.timeout_seconds)
        async with httpx2.AsyncClient(
            headers=headers,
            timeout=timeout,
            follow_redirects=False,
        ) as http_client:
            transport = streamable_http_client(str(server.endpoint), http_client=http_client)
            async with Client(
                transport,
                raise_exceptions=True,
                read_timeout_seconds=server.timeout_seconds,
            ) as client:
                yield client


def _authentication_headers(authentication: Authentication) -> dict[str, str]:
    if authentication.type == "none":
        return {}
    if authentication.credential is None:
        raise PlatformError(
            "AUTHENTICATION_FAILED",
            f"No credential is configured for {authentication.type} authentication",
            status_code=502,
            source="mcp",
        )
    credential = authentication.credential.get_secret_value()
    if authentication.type in {"bearer", "oauth2"}:
        credential = f"Bearer {credential}"
    return {authentication.header_name: credential}


def _content_payload(content: list[Any]) -> list[dict[str, Any]]:
    return [item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in content]
