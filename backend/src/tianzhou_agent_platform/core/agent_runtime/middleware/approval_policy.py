"""Terminal denial of a batch paused by ``HumanInTheLoopMiddleware``.

The built-in middleware answers each rejected call and then lets the loop continue: sibling calls of the paused
batch still run and the model is asked for another answer. A denied batch must instead close every pending call
and end the run without another generation.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage


class TerminalDenialMiddleware(AgentMiddleware):
    """When ``denied``, answer every remaining call with ``tool_result`` and end with ``reply``.

    Keep it in the agent for every invocation (so a resumed graph has the topology it was paused with) and only
    set ``denied`` when resuming a denial. List it first, so its ``before_model`` hook runs before any other.
    """

    def __init__(self, *, denied: bool = False, tool_result: str, reply: str) -> None:
        super().__init__()
        self.denied = denied
        self.tool_result = tool_result
        self.reply = reply

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        if not self.denied:
            return await handler(request)
        return ToolMessage(
            content=self.tool_result,
            tool_call_id=str(request.tool_call.get("id") or ""),
            name=str(request.tool_call.get("name") or ""),
            status="error",
        )

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if not self.denied:
            return None
        return {"messages": [AIMessage(content=self.reply)], "jump_to": "end"}
