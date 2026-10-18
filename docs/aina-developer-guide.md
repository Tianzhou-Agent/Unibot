# Building an AINA app for Unibot

An **AINA** is an app the Unibot agent can use. An AINA Protocol 2.0 app has two parts:

- a **package** you upload to Unibot: `aina.json`, plus optional skills, a canvas and widgets;
- an **MCP server** you run yourself, with the app's tools. Any MCP SDK works; this guide uses the official
  Python SDK and the `unibot-aina` helper.

Users install the package, and the agent then calls your tools. The user can also open your canvas beside the
conversation. You never need Unibot's source code. The full contract is in
[`aina-protocol-v2-design_88989a8d.md`](aina-protocol-v2-design_88989a8d.md). The machine-readable spec is in
[`spec/aina/2.0/`](../spec/aina/2.0):

- [`aina.schema.json`](../spec/aina/2.0/aina.schema.json): the manifest;
- [`host-api.openapi.json`](../spec/aina/2.0/host-api.openapi.json): the Host API;
- [`ui/aina-ui.d.ts`](../spec/aina/2.0/ui/aina-ui.d.ts): the UI bridge.

## Contents

1. [Quick start](#1-quick-start)
2. [The package](#2-the-package)
3. [Tools](#3-tools)
4. [The call context and the Host API](#4-the-call-context-and-the-host-api)
5. [Skills](#5-skills)
6. [Canvas and widgets](#6-canvas-and-widgets)
7. [Permissions, settings and secrets](#7-permissions-settings-and-secrets)
8. [Long-running tools](#8-long-running-tools)
9. [Publishing and upgrades](#9-publishing-and-upgrades)
10. [Troubleshooting](#10-troubleshooting)

## 1. Quick start

You need Python 3.12 for the `aina` CLI. It ships with the Unibot backend package. Your own server can use any
Python from 3.10.

```bash
pip install "git+https://github.com/Tianzhou-Agent/Unibot#subdirectory=backend"   # the aina CLI
aina init notes --id com.example.notes --name "Notes"
cd notes
pip install -r server/requirements.txt     # mcp and unibot-aina (until it is on PyPI:
                                           # pip install "git+https://github.com/Tianzhou-Agent/Unibot#subdirectory=sdk/python")
python server/main.py                      # your MCP server, at http://127.0.0.1:8770/mcp
```

In a second terminal:

```bash
aina validate package --live      # checks aina.json and the files, then lists your server's tools
aina dev package                  # a local Unibot host at http://127.0.0.1:8790
```

`aina dev` runs the real Unibot host on your machine. Its page shows:

- your canvas and widgets, as users will see them;
- tools you can call as the agent would, through the approval step;
- canvas tools you can run by hand;
- every bridge message, MCP call and Host API call.

It re-registers the package when you edit a file in `package/`. Press **Reload** after you restart your server, so
the host lists its tools again.

When the app works, deploy the server on HTTPS and set `tools.mcp.url` in `package/aina.json` to its address. Then
publish:

```bash
aina publish package --host https://unibot.example.com/api --token <developer token>
```

Create the token in Unibot under **Settings → Developer**. It is shown once. It can only validate, publish and
refresh packages, and publishing needs a platform admin.

Without the CLI, use **Plugins → Add → New app** in Unibot to download the same starter project. **Upload app
package** runs the host's checks and lets an admin register it.

## 2. The package

```text
package/
  aina.json              the manifest (required)
  skills/<id>/SKILL.md   workflows the agent loads on demand
  canvas/index.html      the app's main screen (or canvas.url for a hosted web app)
  widgets/*.html         small views in the conversation
  assets/icon.svg
```

A minimal `aina.json`:

```json
{
  "protocol_version": "2.0",
  "aina": {
    "id": "com.example.notes",
    "version": "0.1.0",
    "name": "Notes",
    "description": "Write, find and remove the user's short notes.",
    "publisher": { "id": "example", "name": "Example Inc." }
  },
  "tools": {
    "mcp": { "url": "https://notes.example.com/mcp", "auth": { "type": "none" } },
    "expose": {
      "list_notes": { "effect": "read" },
      "add_note": { "effect": "write" }
    }
  },
  "permissions": [{ "scope": "storage" }]
}
```

### Identity

- **`aina.id`** is reverse-DNS (`^[a-z0-9]+(\.[a-z0-9-]+)+$`) and never changes.
- **`aina.version`** is semver. Any change to the package needs a new version (see §9).
- **`aina.description`** is what the agent reads to decide whether to use your app. Write it in English, for the
  model: say what the app does for the user.
- **`aina.locales`** holds translated display names and descriptions. They are shown to users, never to the model.

### Rules the host checks

`aina validate` and the upload dialog run exactly the host's checks:

- **References:** every referenced file exists. Every name in a skill's `tools` is an exposed or canvas tool. A
  `confirm_widget` points to a widget with `purpose: confirmation`.
- **Tool names** are unique across `tools.expose` and `canvas.tools`.
- **Limits:**
  - at most 32 exposed tools, 16 canvas tools and 16 skills;
  - tool descriptions at most 1,000 characters;
  - skill descriptions at most 300 characters;
  - `SKILL.md` at most 20,000 characters, and each supporting file at most 50,000;
  - the archive at most 20 MB.
- **Language:** text meant for the model (descriptions and skills) should be English; non-ASCII gives a warning.
- **Allowed files:** skill folders hold text files only, and Unibot never runs them. Archives may not contain
  symlinks, absolute paths or `..`.

## 3. Tools

Your MCP server may have many tools. Only those listed in `tools.expose` are ever used. When the package is
registered, Unibot calls `tools/list` and stores a **snapshot** of each exposed tool: description, input and output
schema, annotations. The agent only ever sees the snapshot, and arguments are checked against it.

If the server later changes or removes an exposed tool, Unibot disables that tool for everyone until you publish a
new version. Unibot checks again before every new installation and when an admin refreshes the package.

### Effect: when Unibot asks the user

| `effect` | Asks the user | Retried automatically |
|---|---|---|
| `read` | never | yes, on transient errors |
| `write` | before the first call of the tool (in a conversation, or while a canvas stays open) | no |
| `destructive` | before every call | no |

- **Default:** without an `effect`, Unibot uses the MCP annotations. `destructiveHint` means `destructive`,
  `readOnlyHint` means `read`, and anything else is `write`.
- **Both given:** if the manifest and the annotations both give an effect, the stricter one wins.
- **Duplicates:** `write` and `destructive` tools should de-duplicate on `invocation_id`, because it stays the same
  when Unibot retries.

### Visibility

| `visibility` | Who may call the tool |
|---|---|
| `"model"` | the agent |
| `"app"` | your canvas and widgets |

The default is both. App-only tools (for example `sync_bank`) are never shown to the model.

### Results and errors

- **`content`:** the text is what the model reads. Image and resource blocks reach it only as a placeholder line
  for now.
- **`structuredContent`:** passed to the model as JSON, and to the tool's result widget as data.
- **`isError: true`:** for problems the model should read and fix, such as "unknown category". The model sees a
  tool error with your text.
- **Timeouts:** the default timeout is 30 seconds. Set `timeout_seconds` (at most 120) on the exposed tool. Longer
  work must use a long-running tool (§8).
- **Server authentication:** `tools.mcp.auth` is `none`, `bearer` or `api_key`. The credential comes from a secret
  setting (§7). MCP OAuth 2.0 is not supported yet.

## 4. The call context and the Host API

Every `tools/call` carries `_meta["unibot/context"]`. Read it with `unibot_context(ctx)`:

| Field | Meaning |
|---|---|
| `user_id`, `tenant_id`, `installation_id` | Pairwise ids: stable for your app, different for every other app. Unibot's internal ids never leave the host. |
| `invocation_id` | The tool call; stable across Unibot's retries |
| `source` | `agent`, `canvas`, `widget` or `schedule` (informational only; never use it for authorization) |
| `conversation_id`, `workspace_id` | Pairwise ids of where the call happened, when there is one |
| `granted_permissions`, `config` | What the user allowed, and their non-secret settings |
| `host.api_base`, `token` | The Host API address and this call's bearer token |

### Verify that the call came from Unibot

A server with auth `none` can be called by anyone who knows its URL. Verify each call:

```python
from unibot_aina import ContextVerifier

verifier = ContextVerifier(issuer="https://unibot.example.com/api/aina-host/v1", aina_id="com.example.notes")

@mcp.tool()
async def list_notes(ctx: Context) -> Notes:
    unibot = await verifier.verify(ctx)      # raises ContextError unless your host signed this call
    ...
```

The token is an ES256 JWT with these claims:

- `iss`: the Host API base;
- `aud`: your AINA id;
- `sub`: the pairwise user id;
- `scopes`, plus `exp` at most 10 minutes after `iat`.

The host's public keys are at `/.well-known/aina-host-jwks.json`, next to the API root.

### The Host API

Use `unibot.host` to call the Host API for this user. Each route needs one permission (§7). Tokens of an uninstalled
or paused app stop working at once.

| Client method | Route | Permission |
|---|---|---|
| `storage_get/put/delete/list` | `/storage/kv[/{key}]` | `storage`: private to this installation; 256 KiB per value, 10 MiB and 10,000 keys in total |
| `documents(query)`, `document(name)`, `document_outline(name)` | `GET /documents…` | `documents.read`, in the workspace of the call |
| `put_document`, `delete_document` | `PUT/DELETE /documents/{name}` | `documents.write` |
| `search_memory(query)` | `GET /memory/search` | `memory.read`: the user's own memories |
| `remember(content)` | `POST /memory` (and `PATCH`/`DELETE /memory/{id}`) | `memory.write` |
| `chat(messages)` | `POST /llm/chat` | `llm`: the user's own model, without tools |
| `task_progress/complete/fail` | `/tasks/{id}/…` | the task's own invocation (§8) |

The full API is described in [`host-api.openapi.json`](../spec/aina/2.0/host-api.openapi.json).

## 5. Skills

A skill teaches the agent a workflow, for example "close the month: list expenses, flag duplicates, highlight them on
the canvas, ask before deleting".

```json
"skills": [{
  "id": "notes",
  "name": "Keep notes",
  "description": "Use when the user wants to write down, find or remove short notes.",
  "path": "skills/notes",
  "tools": ["list_notes", "add_note", "delete_note", "highlight_notes"]
}]
```

How skills are loaded:

- While your app is active, the agent sees only each skill's `id`, `name` and `description`.
- It loads `SKILL.md` with `load_skill` when the task matches.
- It can then read other files in the same folder, for example `references/categories.md`.
- A skill may only name tools from its own `tools` list. It cannot add tools or permissions.

## 6. Canvas and widgets

Your canvas and widgets are web pages. Unibot shows them in a sandboxed iframe with no network access and no cookies.
Everything goes through the UI bridge:

- vendor `aina-ui.js` next to your HTML (as `aina init` does), or
- `npm install @unibot/aina-ui` in a bundled app.

```js
import { host } from "./aina-ui.js";

const { locale, config } = await host.getContext();
const { structuredContent } = await host.callTool("list_notes");
host.on("tool_result", refresh);                  // the agent changed data in chat
host.sendPrompt("Summarize my notes");            // speaks in the conversation as the user
```

`callTool` reaches only `app`-visible tools. Unibot asks the user itself before writes and destructive calls, and your
UI can never approve on the user's behalf. Use the `--aina-*` CSS variables (or `aina-ui.css`) to match Unibot's look.

### Canvas

The canvas is the app's main screen, opened beside the conversation. Set `canvas.entry` to an HTML file in the package.

You can set `canvas.url` instead, for a web app you host on HTTPS. That page gets a short-lived identity token from
`host.getIdentityToken()`; your backend checks it with `ContextVerifier.verify_identity_token`.

### Canvas tools

Canvas tools let the agent act on the open canvas, for example "circle the login box". You declare them under
`canvas.tools`, with an `inputSchema` and an `effect`, and implement them in the page:

```js
host.registerTool("highlight_notes", async ({ note_ids }) => {
  highlight(note_ids);
  return { structuredContent: { highlighted: note_ids.length } };
});
host.setModelContext(`The canvas shows ${count} notes.`);   // sent with the agent's requests (at most 4,000 characters)
```

How the calls behave:

- **Routing:** the call goes to the user's most recently active tab. If no tab has the conversation open, the agent
  is told to ask the user to open it.
- **Timeout:** the default is 10 seconds; set `timeout_seconds`, at most 30.
- **Result limits:** `structuredContent` at most 64 KiB, and at most one image of at most 1 MiB.
- **Registration:** call `registerTool` before you load data that the agent's drawings would overwrite.

### Widgets

Widgets are small views inside the conversation:

```json
"tools": { "expose": { "add_note": { "effect": "write", "confirm_widget": "confirm-note", "result_widget": "note-card" } } },
"widgets": [
  { "id": "confirm-note", "purpose": "confirmation", "entry": "widgets/confirm-note.html", "height": 140 },
  { "id": "note-card", "purpose": "result", "entry": "widgets/note-card.html", "height": 120 }
]
```

- **Confirmation widgets** show inside Unibot's approval card. `getContext().arguments` holds the pending call's
  arguments. The widget may `proposeArguments(edited)`; Unibot checks the edited arguments against the schema again,
  and **Allow** and **Deny** stay Unibot's own buttons.
- **Result widgets** show under the agent's answer, rendered from `getContext().result`. They render the same after a
  reload, because they use only the archived result. A result can choose another declared widget with
  `_meta["unibot/widget"] = "<id>"`, or none with `null`.
- **Size:** widgets may `resize(height)` up to 480 px, and `openCanvas()` opens your canvas.

## 7. Permissions, settings and secrets

Declare what your app needs. Users see each scope with your `reason` before they install:

```json
"permissions": [
  { "scope": "storage" },
  { "scope": "documents.read", "reason": "Import receipts you saved as documents.", "optional": true },
  { "scope": "llm", "reason": "Categorize free-text receipts.", "optional": true }
]
```

The scopes with a Host API today are `storage`, `documents.read`, `documents.write`, `memory.read`, `memory.write` and
`llm`. Users may decline optional scopes, so check `granted_permissions` before you use one.

Per-user settings are a JSON Schema under `config.schema`. Unibot shows them as a form when the user installs.

Fields with `"x-secret": true` are stored encrypted and never reach your server in `_meta`. Unibot uses them only
for `tools.mcp.auth`:

```json
"tools": { "mcp": { "url": "https://…/mcp", "auth": { "type": "bearer", "secret": "api_token" } } },
"config": { "schema": { "type": "object", "properties": {
  "api_token": { "type": "string", "x-secret": true, "title": "API token" }
}, "required": ["api_token"] } }
```

## 8. Long-running tools

Mark the tool `"long_running": true` and answer at once. Put a short acknowledgement in `content` and
`_meta["unibot/task"] = {"id": "t_1", "title": "Spending report · 2026-09"}` in the result. Unibot then:

- ends the turn with your acknowledgement;
- lists the task above the chat box;
- posts your result into the conversation when you finish.

Report from your server with the same call's token:

```python
await unibot.host.task_progress("t_1", progress=3, total=10, message="Reading receipts")
await unibot.host.task_complete("t_1", "Your report is ready.", structured={"total": 1234.5})
# or: await unibot.host.task_fail("t_1", "The bank did not answer")
```

The rules:

- **Refreshing the token:** the token expires after 10 minutes. Refresh it with `POST /token/refresh` and
  `{"task_id"}`; that route accepts the expired token of a task that is still running.
- **Deadline:** a task must end within 24 hours.
- **Where tasks start:** long-running tools start only from the conversation, never from your UI.

## 9. Publishing and upgrades

- **Registration:**
  - `aina publish`, or **Upload app package**, registers a version. Registering needs a platform admin.
  - For a `bearer` or `api_key` server, pass `--credential` (or fill the field in the dialog). It is used once to
    list your tools and is not stored.
- **Versions:** publishing the same version with different content is refused. Bump `aina.version`.
- **Upgrades that need consent again:** if a new version widens what your app can do, each installation pauses
  until its user approves again. That includes:
  - a new tool, or a tool newly visible to the agent;
  - an effect that asks less often;
  - a new required permission;
  - a new URL canvas origin.
- **Other upgrades:** every other upgrade moves installations to the new version silently.
- **Server changes:** if your server's exposed tools change without a new package version, those tools are disabled.
  Publish a new version instead.

## 10. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `aina validate --live` cannot connect | The server is not running at `tools.mcp.url`, or it is not Streamable HTTP. Use `stateless_http=True` with the Python SDK. |
| Registration fails with "destination … is not approved" | Your host only allows MCP servers from origins an admin approved. Ask for your origin to be added. |
| A tool says it "changed after review" | The server's description or schema differs from the registered snapshot. Publish a new version. |
| `callTool` fails with "cannot be called from the app's UI" | The tool's `visibility` lacks `"app"`. |
| Host API returns 401 | The token expired (refresh it for tasks), or the app was uninstalled or paused. |
| Host API returns 403 | The permission is not declared or the user did not grant it. |
| The canvas tool call times out | The canvas has not called `registerTool` for it, or its handler takes longer than `timeout_seconds`. |
| Nothing loads in the canvas | The page fetched from the network. Packaged UI has `connect-src 'none'`; use `callTool`. |
