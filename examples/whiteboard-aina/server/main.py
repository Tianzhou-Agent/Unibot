"""Whiteboard AINA tool server: saves and loads the user's board through the Host API ``storage`` scope.

The drawing itself happens in the canvas (canvas tools, design §9.5); this server only persists the scene when the
user presses Save, and lets the agent read the saved board when no canvas is open.
"""

from __future__ import annotations

from typing import Any

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel

MAX_ELEMENTS = 2_000

mcp = MCPServer("whiteboard", version="1.0.0")
# Tests route Host API calls to an in-process Superbot; None means real HTTP.
HOST_TRANSPORT: httpx.AsyncBaseTransport | None = None


class Board(BaseModel):
    scene: list[dict[str, Any]]


def _unibot(ctx: Context) -> dict[str, Any]:
    context = (ctx.request_context.meta or {}).get("unibot/context")
    if not isinstance(context, dict):
        raise ToolError("This server must be called by Superbot")
    return context


async def _host(context: dict[str, Any], method: str, path: str, **kwargs: Any) -> httpx.Response:
    async with httpx.AsyncClient(
        base_url=context["host"]["api_base"],
        headers={"Authorization": f"Bearer {context['token']}"},
        transport=HOST_TRANSPORT,
        timeout=10,
    ) as client:
        return await client.request(method, path, **kwargs)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def load_board(ctx: Context) -> Board:
    """Return the user's saved whiteboard scene (shapes with ids, types, positions, sizes and text)."""
    response = await _host(_unibot(ctx), "GET", "/storage/kv/board")
    if response.status_code == 404:
        return Board(scene=[])
    response.raise_for_status()
    return Board(scene=list(response.json()["value"]))


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
async def save_board(scene: list[dict[str, Any]], ctx: Context) -> str:
    """Save the whiteboard scene (called from the canvas's Save button)."""
    if len(scene) > MAX_ELEMENTS:
        raise ToolError(f"A board holds at most {MAX_ELEMENTS} shapes")
    (await _host(_unibot(ctx), "PUT", "/storage/kv/board", json={"value": scene})).raise_for_status()
    return f"Saved {len(scene)} shape(s)"


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8766, stateless_http=True)
