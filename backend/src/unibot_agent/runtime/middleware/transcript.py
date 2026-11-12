"""Transcript hook: hand every working-state message to the caller before it can be summarized away.

``SummarizationMiddleware`` replaces summarized messages in the checkpointed state. An application that keeps the
complete original transcript elsewhere persists each message here first, so compaction never loses an original.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AnyMessage


class TranscriptHookMiddleware(AgentMiddleware):
    """Call ``on_messages`` with the working-state messages before every model call.

    List it before the summarization middleware so its ``before_model`` hook runs first. ``on_messages`` must be
    idempotent (it sees earlier messages again) and should raise when persistence fails.
    """

    def __init__(self, on_messages: Callable[[list[AnyMessage]], Awaitable[Any]]) -> None:
        super().__init__()
        self.on_messages = on_messages

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        await self.on_messages(list(state["messages"]))
        return None
