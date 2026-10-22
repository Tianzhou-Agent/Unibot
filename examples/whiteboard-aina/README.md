# Whiteboard — AINA Protocol 2.0 canvas tools reference

The reference AINA for **canvas tools** ([design §9.5](../../docs/aina-protocol-v2-design_88989a8d.md)): tools that
the open canvas implements in the user's browser, so the agent can change what the user sees. For example, "draw a
circle around the login box" is drawn live on the whiteboard.

| Part | Where |
|---|---|
| Canvas + canvas tools | `package/canvas/app.js`: a small Excalidraw-style SVG board. Its canvas tools are `get_scene`, `draw_circle`, `draw_rectangle`, `draw_arrow`, `add_text` and `clear_scene` (destructive, so Superbot asks first) |
| Model context | the canvas calls `host.setModelContext(summary)` on every change, so the agent sees the board without calling `get_scene` |
| Tools (MCP) | `server/main.py`: `load_board` (model + app) and `save_board` (app only), kept in Host API `storage` |
| Skill | `package/skills/sketch/`: how to lay out and annotate a diagram |

The scene lives in the browser. Save stores it with `save_board`, and the canvas loads it again with `load_board`
before it registers its tools, so a drawing the agent makes is never replaced by the saved board. While the user is
away the canvas runs in Superbot's headless canvas host (`getContext().background`), where nobody presses Save, so
there each change the agent makes is saved before its tool answers and the tab the user opens later loads it.

## Try it

```bash
cd examples/whiteboard-aina/server && python main.py      # MCP server on http://127.0.0.1:8766/mcp
aina pack examples/whiteboard-aina/package -o whiteboard.aina.zip
```

Register the zip as a platform admin (`POST /ainas/packages`) and install it from the Plugins page. Then, in a chat,
ask "draw a login box and circle it". The canvas opens by itself if it is closed, and Superbot asks before the first
drawing call. With the conversation open in two tabs, only the tab you looked at last draws. Without any open tab,
the agent is told the canvas is not open.
