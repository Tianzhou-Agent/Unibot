# Expenses — AINA Protocol 2.0 example

The reference package for [AINA Protocol 2.0](../../docs/aina-protocol-v2-design_88989a8d.md). It has all four
parts of a 2.0 AINA:

| Part | Where |
|---|---|
| Tools | `server/main.py` — a plain MCP server (official Python SDK, Streamable HTTP) |
| Skills | `package/skills/record-expense/`, `package/skills/monthly-close/` |
| Canvas | `package/canvas/` (with the `highlight_rows` canvas tool) |
| Widgets | `package/widgets/confirm-expense.html`, `package/widgets/expense-card.html` |

Everything Unibot needs to know is in `package/aina.json`.

## Try it

```bash
cd examples/expenses-aina/server && python main.py      # MCP server on http://127.0.0.1:8765/mcp
aina validate examples/expenses-aina/package --live     # schema, files, budgets and a live tools/list check
aina pack examples/expenses-aina/package -o expenses.aina.zip
```

Register the zip as a platform admin with `POST /ainas/packages` (multipart field `file`), then install it from the
Plugins page, or with `POST /ainas/com.example.expenses/install`. `storage` is granted automatically. Set
`UNIBOT_AINA_HOST_API_BASE` to an address of Unibot that this server can reach. The loopback MCP origin must be in
`UNIBOT_CAPABILITY_ALLOWED_ORIGINS` when destination policy is enforced.

## What works today

The protocol ships in phases (design §15). This package is written against the frozen P0 schema, so it does not
change as later phases land:

- **P0–P2 (implemented):** validation, MCP tool snapshot and drift check, manifest-driven scope, effect-based
  approvals, skills, the context token, and Host API `storage`. The server keeps no data of its own: every expense is
  read and written through `storage`, scoped by the token to the calling user.
- **Long-running tool:** `monthly_report` is `long_running`. It answers at once with `_meta["unibot/task"]`, which
  ends the turn, then builds the report in the background and reports on the Host API (`/tasks/{id}/progress`, then
  `/complete` or `/fail`). It renews its context token with `/token/refresh` if the token expires. Unibot posts the
  report to the conversation and shows the task in the Tasks pane. The jobs live in process memory, so a restart
  loses them; a real server would use a durable queue.
- **Later:** canvas, widgets and the `aina-ui.js` bridge the HTML files import (P3), and canvas tools such as
  `highlight_rows` (P6).
