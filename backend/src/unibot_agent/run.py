"""Per-invocation context shared by the capability tools and middleware of one agent run.

Created for each turn or approval continuation and never checkpointed: it holds the event sinks, the mutable
capability scope and the capabilities registered as tools for this invocation. The capability provider receives it
with every call and changes the run through it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import ToolMessage

from unibot_agent.capabilities import Capability
from unibot_agent.scope import RunScope

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


def artifact_refs(artifacts: list[Any]) -> list[dict[str, Any]]:
    """How artifacts are named in trace events: by the ``id`` and ``kind`` they may carry."""
    return [{"id": getattr(item, "id", None), "kind": getattr(item, "kind", None)} for item in artifacts]


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
    # What the memory service recalled for this turn's message.
    memories: list[Any] = field(default_factory=list)
    # Results of capability calls that returned, by call id. A stop can cancel the tool step before LangGraph saves
    # them, and a finished call must then keep its real result instead of being closed as interrupted.
    finished_tool_results: dict[str, ToolMessage] = field(default_factory=dict)
    # The scope the run is in (``CapabilityScope.id``); kept in the run's working memory for a continuation.
    scope_id: str | None = None
    # Whatever the capability provider wants returned with the turn's result (``TurnResult.values``).
    values: dict[str, Any] = field(default_factory=dict)

    @property
    def artifacts(self) -> list[Any]:
        """What capability calls produced for the user to see with the turn's answer; opaque to the core."""
        return self.scope.artifacts

    @artifacts.setter
    def artifacts(self, value: list[Any]) -> None:
        self.scope.artifacts = value

    def activate(
        self, scope_id: str, visible: dict[str, Capability], system_prompt: str, *, entry: str
    ) -> None:
        """Switch the following model requests to another scope; ``entry`` is the function whose call did it."""
        self.scope.visible = dict(visible)
        self.scope.system_prompt = system_prompt
        self.scope.activated_entries.add(entry)
        self.scope_id = scope_id
        self.registry.update(visible)

    def reply_directly(self, text: str | None) -> None:
        """Complete the turn with this text instead of asking the model for an answer."""
        self.scope.direct_reply = text

    def show_images(self, call_id: str, name: str, images: list[dict[str, str]]) -> None:
        """Images a call returned (``[{data, mimeType}]``), for the model of this run only; never stored."""
        self.scope.tool_images[call_id] = (name, images)

    async def emit(self, event: dict[str, Any]) -> None:
        """Send a live application event to the streaming client, if any."""
        if self.event_sink is not None:
            await self.event_sink(event)

    async def push(self, kind: str, status: str, **fields: Any) -> None:
        await self.events.push(self.trace_id, kind=kind, status=status, conversation_id=self.conversation_id, **fields)
