"""Helper for AINA Protocol 2.0 MCP servers (design §12): the Unibot context of a call and a Host API client.

    from unibot_aina import unibot_context

    @mcp.tool()
    async def add_expense(amount: float, ctx: Context) -> dict:
        unibot = unibot_context(ctx)
        items = await unibot.host.storage_get("expenses", default=[])
        ...

``ContextVerifier`` checks that a call was signed by your Unibot host. The package depends only on httpx and
cryptography, never on Unibot's own code, so a bundled first-party server can move to its own process unchanged.
It is published as ``unibot-aina`` from ``sdk/python``.
"""

from unibot_aina.context import UnibotContext, unibot_context
from unibot_aina.host import HostApi, HostApiError, PassedFile, set_default_transport
from unibot_aina.verify import ContextError, ContextVerifier

__all__ = [
    "ContextError",
    "ContextVerifier",
    "HostApi",
    "HostApiError",
    "PassedFile",
    "UnibotContext",
    "set_default_transport",
    "unibot_context",
]
