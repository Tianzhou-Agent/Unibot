# Excalidraw — a remote AINA from an existing MCP server

This app connects Unibot to a local Excalidraw whiteboard. The whiteboard and its MCP server are
[yctimlin/mcp_excalidraw](https://github.com/yctimlin/mcp_excalidraw), unchanged. Unibot adds nothing to the server.
Everything Unibot needs is in `package/aina.json` and one skill.

| Part | Where |
|---|---|
| Tools | The Excalidraw MCP server, served over HTTP by `bridge/serve-http.mjs` |
| Skills | `package/skills/draw-diagram/` |
| Canvas | None: open the whiteboard at http://127.0.0.1:3000. A URL canvas must be `https://` |

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

Unibot must allow the MCP origin: set `UNIBOT_CAPABILITY_ALLOWED_ORIGINS=http://127.0.0.1:3001`. Then install
**Excalidraw** from the Plugins page and ask, for example, "draw our login flow on the whiteboard".

## What is exposed

22 of the server's 26 tools. Drawing and editing tools are `write`, so the user approves each call. Deleting
elements, restoring a snapshot and clearing the board are `destructive`. These tools are left out:

- `export_to_excalidraw_url`: uploads the diagram to excalidraw.com.
- `export_scene`, `export_to_image` and `import_scene`: they read and write files on the machine that runs the
  server.
