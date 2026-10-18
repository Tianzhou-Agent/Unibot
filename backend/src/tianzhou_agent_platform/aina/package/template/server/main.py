"""__NAME__ MCP server (AINA Protocol 2.0).

Run it with `python server/main.py`, then check the package against it with `aina validate package --live`.
Notes are kept in the user's private AINA storage through the Unibot Host API (the `storage` permission).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from unibot_aina import unibot_context

mcp = MCPServer(__SERVER_NAME__, version=__SERVER_VERSION__)


class Note(BaseModel):
    id: str
    text: str
    created_at: str


class Notes(BaseModel):
    items: list[Note]


async def _notes(ctx: Context) -> list[dict[str, str]]:
    return list(await unibot_context(ctx).host.storage_get("notes", default=[]))


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
async def list_notes(ctx: Context) -> Notes:
    """List the user's notes, newest first."""
    return Notes(items=[Note(**note) for note in reversed(await _notes(ctx))])


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
async def add_note(text: Annotated[str, Field(min_length=1, max_length=500)], ctx: Context) -> Note:
    """Save one short note for the user."""
    note = Note(id=f"n_{uuid.uuid4().hex[:8]}", text=text.strip(), created_at=datetime.now(UTC).isoformat())
    await unibot_context(ctx).host.storage_put("notes", [*await _notes(ctx), note.model_dump()])
    return note


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
async def delete_note(note_id: str, ctx: Context) -> str:
    """Delete one of the user's notes by its id."""
    notes = await _notes(ctx)
    kept = [note for note in notes if note["id"] != note_id]
    if len(kept) == len(notes):
        raise ToolError(f"No note {note_id!r}")
    await unibot_context(ctx).host.storage_put("notes", kept)
    return f"Deleted note {note_id}"


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=__PORT__, stateless_http=True)
