# Excalidraw — a remote AINA from an existing MCP server

This app connects Unibot to a local Excalidraw whiteboard. The whiteboard and its MCP server are
[yctimlin/mcp_excalidraw](https://github.com/yctimlin/mcp_excalidraw), unchanged. Unibot adds nothing to the server.
Everything Unibot needs is in `package/aina.json` and one skill.

| Part | Where |
|---|---|
| Tools | The Excalidraw MCP server, served over HTTP by `bridge/serve-http.mjs` |
| Skills | `package/skills/draw-diagram/` |
| Canvas | The whiteboard itself, `http://127.0.0.1:3000/`, as a URL canvas |

## Why a bridge

The Excalidraw MCP server speaks only stdio, and Unibot reaches MCP servers over Streamable HTTP. The bridge loads the
server's own tool table and its bundled MCP SDK (`createMcpHandler`) from the Excalidraw checkout and serves them at
`http://127.0.0.1:3001/mcp`. Tools draw on the canvas service at `http://127.0.0.1:3000`, as the stdio server does.

## Try it

```bash
EXCALIDRAW_DIR=/path/to/excalidraw node examples/excalidraw-aina/bridge/serve-http.mjs
aina validate examples/excalidraw-aina/package --live
aina publish examples/excalidraw-aina/package --host http://127.0.0.1:8000 --token <developer token>
```

Unibot must allow both origins: set `UNIBOT_CAPABILITY_ALLOWED_ORIGINS=http://127.0.0.1:3001` for the tools and
`UNIBOT_AINA_CANVAS_ALLOWED_ORIGINS=http://127.0.0.1:3000` for the whiteboard. Then install
**Excalidraw** from the Plugins page and ask, for example, "draw our login flow on the whiteboard".

## What is exposed

25 of the server's 26 tools:

- **Read (never asks):** reading the guide, describing and querying the board, and taking a screenshot.
- **Write (asks before the first call):** drawing and editing, snapshots, and saving the board as `.excalidraw`,
  PNG or SVG. Files can only be saved in the Excalidraw checkout's `exports/` folder.
- **Destructive (asks before every call):** deleting elements, restoring a snapshot, clearing the board, and
  importing a scene, which can replace the board.

`export_to_excalidraw_url` is left out, because it uploads the diagram to excalidraw.com.
