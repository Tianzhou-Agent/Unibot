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

Register the zip as a platform admin with `POST /ainas/packages` (multipart field `file`), then install it with
`POST /ainas/com.example.expenses/install` and grant the required `storage` scope. The loopback MCP origin must be in
`UNIBOT_CAPABILITY_ALLOWED_ORIGINS` when destination policy is enforced.

## What works today

The protocol ships in phases (design §15). This package is written against the frozen P0 schema, so it does not
change as later phases land:

- **P0/P1 (implemented):** validation, MCP tool snapshot and drift check, manifest-driven scope, effect-based
  approvals, skill index and `load_skill`.
- **Later phases:** Host API and context token, `bearer`/`oauth2` MCP auth and secret config (P2); canvas, widgets and
  the `aina-ui.js` bridge the HTML files import (P3); canvas tools such as `highlight_rows` (P6). Until P2 the server
  keeps expenses in process memory instead of Host API `storage`.
