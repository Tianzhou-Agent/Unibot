"""PPTist AINA tool server: keeps the user's decks in their private AINA storage through the Superbot Host API.

The editing happens in the canvas (PPTist, through canvas tools). This server only lists, loads, saves and deletes
decks: each deck is one PPTist JSON file (``decks/<id>.json``, the format PPTist's own JSON export writes), and the
storage key ``decks`` indexes them. Each conversation has its own deck: the storage key ``conversations/<id>`` holds the
id of the deck last saved in that conversation, which the canvas opens there.

Configuration (environment):
  UNIBOT_HOST_API_BASE  your Superbot's public Host API base, e.g. https://unibot.example.com/api/aina-host/v1. Every
                        call's context token must be issued by it (the server is reachable by anyone who knows its URL).
  UNIBOT_HOST_API_URL   optional: where this server reaches that Host API, when it differs from the public base
                        (e.g. http://host.docker.internal:8000/aina-host/v1 from a container on a developer machine).
  AINA_ID               the package's aina.id (default local.pptist).
  HOST, PORT            where to listen (default 127.0.0.1:8782).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Annotated, Any

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from unibot_aina import ContextError, ContextVerifier, HostApi, UnibotContext, set_default_transport

AINA_ID = os.environ.get("AINA_ID", "local.pptist")
HOST_API_BASE = os.environ.get("UNIBOT_HOST_API_BASE", "http://127.0.0.1:8000/aina-host/v1").rstrip("/")
HOST_API_URL = os.environ.get("UNIBOT_HOST_API_URL", "").rstrip("/")
# Superbot's Host API keeps files of at most 10 MiB, and its web server takes request bodies of at most 12 MB.
MAX_DECK_BYTES = 9 * 1024 * 1024
INDEX_KEY = "decks"

DeckId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,40}$", description="The deck's id, as list_decks shows it.")]

mcp = MCPServer("pptist", version="1.0.0")

if HOST_API_URL:
    set_default_transport(httpx.AsyncHTTPTransport(), api_base=HOST_API_URL)
verifier = ContextVerifier(
    issuer=HOST_API_BASE,
    aina_id=AINA_ID,
    jwks_url=f"{(HOST_API_URL or HOST_API_BASE).removesuffix('/aina-host/v1')}/.well-known/aina-host-jwks.json",
)


class DeckInfo(BaseModel):
    id: str
    title: str
    slide_count: int
    updated_at: str


class Decks(BaseModel):
    items: list[DeckInfo]
    last_opened: str | None = None


class LoadedDeck(BaseModel):
    id: str | None
    deck: dict[str, Any] | None


async def _context(ctx: Context) -> UnibotContext:
    try:
        return await verifier.verify(ctx)
    except (ContextError, ValueError) as exc:
        raise ToolError(f"This server only answers calls from its Superbot host: {exc}") from exc


async def _host(ctx: Context) -> HostApi:
    return (await _context(ctx)).host


async def _index(host: HostApi) -> dict[str, Any]:
    return dict(await host.storage_get(INDEX_KEY, default={"items": [], "last": None}))


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
async def list_decks(ctx: Context) -> Decks:
    """List the user's saved PPTist decks, most recently saved first."""
    index = await _index(await _host(ctx))
    items = sorted((DeckInfo(**item) for item in index["items"]), key=lambda item: item.updated_at, reverse=True)
    return Decks(items=items, last_opened=index.get("last"))


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
async def load_deck(ctx: Context, id: DeckId | None = None) -> LoadedDeck:
    """Return a saved deck as PPTist JSON (title, width, height, theme, slides); without an id, the deck of the
    conversation the canvas is open beside, if it has one."""
    context = await _context(ctx)
    host = context.host
    deck_id = id
    if deck_id is None and context.conversation_id:
        deck_id = await host.storage_get(f"conversations/{context.conversation_id}")
    data = await host.get_file(f"decks/{deck_id}.json") if deck_id else None
    if data is None:
        return LoadedDeck(id=None, deck=None)
    return LoadedDeck(id=deck_id, deck=json.loads(data))


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False))
async def save_deck(id: DeckId, deck: dict[str, Any], ctx: Context, bind: bool = True) -> DeckInfo:
    """Save the deck open in the canvas (the canvas calls this as the user edits). With ``bind``, it also becomes the
    deck of the conversation the canvas is open beside; the canvas saves without it what was open before it moved."""
    slides = deck.get("slides")
    if not isinstance(slides, list) or not slides:
        raise ToolError("A deck needs a non-empty slides list")
    data = json.dumps(deck, ensure_ascii=False, separators=(",", ":")).encode()
    if len(data) > MAX_DECK_BYTES:
        raise ToolError(f"The deck is {len(data) // (1024 * 1024)} MiB; a saved deck holds at most 9 MiB of slides and pictures")
    context = await _context(ctx)
    host = context.host
    await host.put_file(f"decks/{id}.json", data, "application/json")
    if bind and context.conversation_id:
        await host.storage_put(f"conversations/{context.conversation_id}", id)
    info = DeckInfo(
        id=id,
        title=str(deck.get("title") or "Untitled"),
        slide_count=len(slides),
        updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    index = await _index(host)
    index["items"] = [item for item in index["items"] if item["id"] != id] + [info.model_dump()]
    index["last"] = id
    await host.storage_put(INDEX_KEY, index)
    return info


@mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True))
async def delete_deck(id: DeckId, ctx: Context) -> str:
    """Delete one of the user's saved decks for good."""
    host = await _host(ctx)
    index = await _index(host)
    kept = [item for item in index["items"] if item["id"] != id]
    if len(kept) == len(index["items"]):
        raise ToolError(f"There is no saved deck {id!r}")
    await host.delete_file(f"decks/{id}.json")
    index["items"] = kept
    if index.get("last") == id:
        index["last"] = None
    await host.storage_put(INDEX_KEY, index)
    return f"Deleted deck {id}"


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8782")),
        stateless_http=True,
    )
