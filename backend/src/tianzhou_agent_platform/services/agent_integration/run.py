"""Per-invocation context shared by the capability tools and middleware of one agent run.

Created for each turn or approval continuation and never checkpointed: it holds the event sinks, the mutable
capability scope and the capabilities registered as tools for this invocation.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from tianzhou_agent_platform.aina.memory.models import MemoryRecord
from tianzhou_agent_platform.conversations.models import Conversation
from tianzhou_agent_platform.services.agent_integration.capabilities import Capability
from tianzhou_agent_platform.services.agent_integration.scope import RunScope

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class AgentRun:
    trace_id: str
    conversation_id: str
    user_id: str
    tenant_id: str
    workspace_id: str | None
    events: Any
    scope: RunScope
    # Every capability registered as a tool for this invocation; the scope decides which are advertised.
    registry: dict[str, Capability]
    event_sink: EventSink | None = None
    memory_context: list[MemoryRecord] = field(default_factory=list)

    async def emit(self, event: dict[str, Any]) -> None:
        """Send a live application event to the streaming client, if any."""
        if self.event_sink is not None:
            await self.event_sink(event)

    async def push(self, kind: str, status: str, **fields: Any) -> None:
        await self.events.push(self.trace_id, kind=kind, status=status, conversation_id=self.conversation_id, **fields)

    async def scope_resolved(
        self,
        conversation: Conversation,
        *,
        selected: Capability | None,
        source: str,
        requested_capability: str | None = None,
        preferred_aina_id: str | None = None,
    ) -> None:
        await self.push(
            "routing.scope.resolved",
            "completed",
            target_type=selected.kind if selected is not None else "system",
            target_id=selected.capability_id if selected is not None else None,
            details={
                "source": source,
                "requested_capability": requested_capability,
                "preferred_aina_id": preferred_aina_id,
                "active_aina_ids": conversation.active_aina_ids,
                "primary_aina_id": conversation.primary_aina_id,
                "last_aina_id": conversation.last_aina_id,
            },
        )
