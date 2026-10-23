# PPTist Slides: a slide editor as a remote AINA

This app puts [PPTist](https://github.com/pipipi-pikachu/PPTist), an open-source PowerPoint-style editor, beside the
conversation. The agent drafts decks from an outline with PPTist's own designed templates, edits slides the user has
open, checks their layout, and exports .pptx. The user edits the same deck by hand at the same
time. Decks are saved in the user's private AINA storage, and each conversation has its own deck, so the canvas shows
it again whenever the user returns to that conversation.

PPTist itself is not changed. It is built from a pinned commit with one extra module, the bridge, which shares the
editor's stores and hooks.

| Part | Where |
|---|---|
| Canvas | PPTist plus `canvas/bridge/aina-bridge.ts`, built by `canvas/Dockerfile` and served by nginx (a URL canvas) |
| Canvas tools | `aina-bridge.ts`: 13 tools that read, change, render and export the open deck |
| Tools (MCP) | `server/main.py`: `list_decks`, `load_deck`, `save_deck`, `delete_deck`, kept in Host API storage |
| Skill | `package/skills/make-deck/`, with PPTist's AI slide schema as its reference |
| Deployment | `compose.yml`: both services behind one port; nginx serves the editor at `/` and proxies `/mcp` |

## Why a URL canvas

A packaged canvas runs with an opaque origin: no IndexedDB, and no network (`connect-src 'none'`). PPTist keeps its
undo history in IndexedDB and loads its templates over HTTP, so it runs as a URL canvas from its own origin. nginx only
lets Superbot frame it (`frame-ancestors`). The tool server sits behind the same origin, at `/mcp`, so one HTTPS host
serves the whole app.

## Try it on your machine

With a Superbot backend at `http://127.0.0.1:8000` and its web app at `http://127.0.0.1:5173`:

```bash
docker compose -f examples/pptist-aina/compose.yml up -d --build    # http://127.0.0.1:8781 (editor and /mcp)
```

Start the backend with both origins allowed:

```bash
UNIBOT_CAPABILITY_ALLOWED_ORIGINS=http://127.0.0.1:8781 \
UNIBOT_AINA_CANVAS_ALLOWED_ORIGINS=http://127.0.0.1:8781 \
uv run tianzhou-agent-platform
```

On a development machine you can set both to `*` once in `backend/.env` to allow any origin, so a new app needs no
change there.

Then validate and publish the package:

```bash
aina validate examples/pptist-aina/package --live
aina publish examples/pptist-aina/package --host http://127.0.0.1:8000 --token <developer token>
```

Install **PPTist Slides** from the Plugins page, open it, and ask, for example, "make a 6-slide intro to our project".

The tool server runs in Docker and reaches the backend on your machine at `host.docker.internal`
(`UNIBOT_HOST_API_URL`). The backend reaches the tool server at `127.0.0.1:8781`, so this local setup needs the backend
on the host (`uv run`), not inside Superbot's own compose stack. For that, use HTTPS as below.

## Deploy it

1. **Build and run** `compose.yml` on a host behind your HTTPS reverse proxy, for example at
   `https://slides.example.com`. Set:

   | Variable | Value |
   |---|---|
   | `UNIBOT_ORIGIN` | Superbot's web app origin(s) that may frame the editor, e.g. `https://unibot.example.com` |
   | `UNIBOT_HOST_API_BASE` | Superbot's public Host API base (its `UNIBOT_AINA_HOST_API_BASE`), e.g. `https://unibot.example.com/api/aina-host/v1` |
   | `UNIBOT_HOST_API_URL` | where the tool server reaches that Host API; the same value when it is reachable as is |
   | `PPTIST_PORT` | the local port your proxy forwards to (default `8781`, bound to 127.0.0.1) |

   The proxy forwards everything to that port, including `/mcp`. Allow request bodies of 12 MB, and do not buffer
   `/mcp` responses.
2. **Point the package at it.** In `package/aina.json`, set `tools.mcp.url` to `https://slides.example.com/mcp` and
   `canvas.url` to `https://slides.example.com/`.
3. **Allow the origin in Superbot.** Add `https://slides.example.com` to both `UNIBOT_CAPABILITY_ALLOWED_ORIGINS` and
   `UNIBOT_AINA_CANVAS_ALLOWED_ORIGINS`.
4. **Publish** the package with `aina publish` (or **Plugins → Add**), then install it.

The tool server checks every call's context token against `UNIBOT_HOST_API_BASE`, so it answers only your Superbot,
even though `/mcp` is public. If you change `aina.id`, set `PPTIST_AINA_ID` to match.

## What the agent can do

Canvas tools, which run in the user's open editor:

- **Read (never asks):** `get_deck` (slides and their elements), `get_slide` (one slide's full JSON), `render_slide`
  (a picture of a slide), `go_to_slide`.
- **Write (asks before the first call):** `generate_deck` (a new deck from an outline and one of PPTist's 8
  templates), `add_slides`, `update_slide`, `update_elements`, `new_deck`, `open_deck`, `set_title`, `export_pptx`.
- **Destructive (asks every time):** `delete_slides`.

MCP tools: `list_decks` (also works with the canvas closed), `delete_deck` (destructive), and `load_deck` and
`save_deck`, which only the canvas calls.

Every change the agent makes is a step in PPTist's undo history. Text the agent writes is reduced to the tags and
styles of PPTist's schema before it is shown, and pictures must be `https://` or `data:image/` URLs.

## Saving

The canvas saves the open deck on its own, 1.5 seconds after a change, with `save_deck`; Superbot does not ask, since
the canvas calls its own tool. If a save fails, the canvas stops saving and says so. A saved deck is one
PPTist JSON file (the format of PPTist's own JSON export) of at most 9 MiB, including uploaded pictures.

Each conversation has its own deck: the one last saved or opened in it (`conversations/<id>` in storage holds its id).
When the user switches conversations with the canvas open, Superbot sends `context_changed`; the canvas saves the open
deck and shows that conversation's deck, or a blank one if it has none yet. A deck started before the conversation
existed (in a new chat) becomes that conversation's deck when the first message creates it. `list_decks` and
`open_deck` still reach every saved deck, so a deck from another conversation can be opened here; it then becomes this
conversation's deck too.

`export_pptx` exports the open deck as .pptx and hands it to Superbot with the UI bridge's `saveFile`: Superbot asks the
user once per opened canvas, keeps the file in `Uploads/` of their Files (the workspace's, beside a workspace
conversation), and downloads it from its own page, which a sandboxed frame cannot always do. While the user is away (the
headless canvas host), the file is only saved to Files. The bridge takes the file from PPTist's own export (pptxgenjs's
`writeFile`), so PPTist is not changed. PPTist's own export menu still downloads in the frame and saves nothing.

## Upgrading PPTist

Change `PPTIST_COMMIT` in `canvas/Dockerfile` and rebuild. The build type-checks the bridge against the new PPTist, so
a store or hook that changed fails the build rather than the canvas. `prepare.sh` also stops if `index.html` no longer
loads `src/main.ts`.

## Known limits

- PPTist's interface is in Chinese only.
- PPTist's own AI buttons (AI deck, AI writing, picture search) call PPTist's public service at `server.pptist.cn`.
  The Superbot agent does not use them. Block that host at your egress if users must not reach it.
- The canvas pane is narrower than PPTist's layout. Expand the pane for comfortable editing.
- The templates keep a few fixed Chinese labels; the skill tells the agent to replace them in decks in other languages.
- Superbot replaces pictures in canvas tool results with `[image omitted]`, so `render_slide` does not let the agent see
  a slide yet. The skill has the agent check layout from slide geometry instead.

## License

PPTist is AGPL-3.0, like Superbot, and so is the canvas image built from it. `skills/make-deck/references/slide-schema.md`
is PPTist's `doc/AI_PPT_SCHEMA.md`, unchanged.
