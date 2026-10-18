"""Helper for AINA Protocol 2.0 MCP servers (design §12): the Unibot context of a call and a Host API client.

    from unibot_aina import unibot_context

    @mcp.tool()
    async def add_expense(amount: float, ctx: Context) -> dict:
        unibot = unibot_context(ctx)
        items = await unibot.host.storage_get("expenses", default=[])
        ...

It depends only on the MCP SDK and httpx, never on Unibot's own code, so a bundled first-party server can move to its
own process unchanged.
"""

from unibot_aina.context import UnibotContext, unibot_context
from unibot_aina.host import HostApi, HostApiError, set_default_transport

__all__ = ["HostApi", "HostApiError", "UnibotContext", "set_default_transport", "unibot_context"]
