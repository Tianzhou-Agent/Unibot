"""Optional Code Runner MCP server; scripts run in a separately deployed sandboxd."""

from __future__ import annotations

import os
from typing import Annotated, Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

mcp = MCPServer("code-runner", version="2.0.0")
# Tests replace the sandboxd transport; normal operation uses HTTP.
SANDBOX_TRANSPORT: httpx.AsyncBaseTransport | None = None
Script = Annotated[str, Field(min_length=1, max_length=200_000)]
Timeout = Annotated[int, Field(ge=1, le=110)]
Directory = Annotated[str, Field(max_length=500)]


async def _execute(language: str, script: str, timeout_seconds: int, working_directory: str) -> dict[str, Any]:
    base_url = os.environ.get("CODE_RUNNER_SANDBOX_URL", "http://127.0.0.1:18082").rstrip("/")
    try:
        async with httpx.AsyncClient(transport=SANDBOX_TRANSPORT, timeout=timeout_seconds + 10) as client:
            response = await client.post(
                f"{base_url}/exec",
                json={
                    "language": language,
                    "script": script,
                    "timeout_seconds": timeout_seconds,
                    "working_directory": working_directory,
                },
            )
            response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ToolError("SANDBOX_UNAVAILABLE: sandboxd could not execute the script") from exc
    return dict(response.json())


@mcp.tool()
async def run_python(
    script: Script, timeout_seconds: Timeout = 60, working_directory: Directory = "."
) -> dict[str, Any]:
    """Run Python in the configured sandbox; return stdout, stderr, exit code and execution status."""
    return await _execute("python", script, timeout_seconds, working_directory)


@mcp.tool()
async def run_bash(script: Script, timeout_seconds: Timeout = 60, working_directory: Directory = ".") -> dict[str, Any]:
    """Run Bash in the configured sandbox, including dependency installation and file processing."""
    return await _execute("bash", script, timeout_seconds, working_directory)


@mcp.tool()
async def run_node(script: Script, timeout_seconds: Timeout = 60, working_directory: Directory = ".") -> dict[str, Any]:
    """Run Node.js in the configured sandbox; return stdout, stderr, exit code and execution status."""
    return await _execute("node", script, timeout_seconds, working_directory)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8768, stateless_http=True)
