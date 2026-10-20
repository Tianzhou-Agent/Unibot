# unibot-aina

Helper for the MCP server of a [Superbot](https://github.com/Tianzhou-Agent/Unibot) AINA Protocol 2.0 app. It works
with any MCP server SDK; the examples use the official MCP Python SDK.

```bash
pip install unibot-aina
```

```python
from mcp.server.mcpserver import Context, MCPServer
from unibot_aina import ContextVerifier, unibot_context

mcp = MCPServer("com.example.notes")
verifier = ContextVerifier(issuer="https://unibot.example.com/api/aina-host/v1", aina_id="com.example.notes")


@mcp.tool()
async def list_notes(ctx: Context) -> dict:
    unibot = await verifier.verify(ctx)       # or unibot_context(ctx) to read the context without checking it
    notes = await unibot.host.storage_get("notes", default=[])
    return {"items": notes}
```

- `unibot_context(ctx)` reads `_meta["unibot/context"]`: pairwise user, tenant and installation ids, the invocation's
  source, the user's settings and the Host API access of this call.
- `context.host` is a typed Host API client (`storage_*`, `documents`/`put_document`, `search_memory`/`remember`,
  `chat`, `task_progress`/`task_complete`/`task_fail`), limited to the permissions the user granted.
- `ContextVerifier` checks the call's ES256 context token against your host's published key, issuer, your AINA id and
  expiry. Use it when your server is reachable by others; `verify_identity_token` does the same for a URL canvas's
  identity token.

See the AINA developer guide (`docs/aina-developer-guide.md` in the Superbot repository).
