"""The services an agent needs, as the core calls them.

A host implements these and hands them to ``unibot_agent.agent.Agent``. The core knows what each call means, never
how it is carried out. Working memory is the one service without a protocol here: it is a LangGraph
``BaseCheckpointSaver``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Any, Protocol

from unibot_agent.capabilities import Capability, CapabilityScope
from unibot_agent.types import ApprovalRequest, Message, ModelCallRecord, ModelHandle, Session, TurnRequest

if TYPE_CHECKING:
    from unibot_agent.run import AgentRun


class SessionStore(Protocol):
    """Required. The complete archive of a conversation's messages."""

    async def history(self, session: Session) -> list[Message]:
        """The archived messages, as working memory starts from them when the agent has none."""
        ...

    async def append_messages(self, session_id: str, messages: Sequence[Message]) -> None:
        """Archive messages; one with an id that is already archived is left as it is."""
        ...

    async def take_external_messages(self, session_id: str) -> list[Message]:
        """Messages others added to the archive that the model has not seen; each is returned once."""
        ...


class ModelProvider(Protocol):
    """Required. The chat model a run uses."""

    def resolve(self, run: AgentRun) -> ModelHandle: ...


class CapabilityProvider(Protocol):
    """Required. What the model may call in a run, the instructions that go with it, and the calls themselves.

    While executing a call the provider may change the run through ``AgentRun``: switch its scope (``activate``),
    answer the user itself (``reply_directly``), add what the user should see (``artifacts``) and send live events.
    """

    async def open_scope(
        self, run: AgentRun, session: Session, request: TurnRequest, *, after_failed_turn: bool
    ) -> CapabilityScope:
        """The scope a new turn starts in."""
        ...

    async def resume_scope(
        self, run: AgentRun, session: Session, stored_scope_id: Callable[[], Awaitable[str | None]]
    ) -> CapabilityScope:
        """The scope of a turn continued after a stop or an approval.

        ``stored_scope_id`` reads the id of the scope the turn started in from its working memory.
        """
        ...

    async def execute(self, run: AgentRun, capability: Capability, call_id: str, arguments: dict[str, Any]) -> str:
        """Perform one call and return its result as the model reads it."""
        ...

    async def request_context(self, run: AgentRun) -> str:
        """Text appended to the instructions of the next model request (state that changes during the run)."""
        ...


class ApprovalStore(Protocol):
    """Needed once a capability requires confirmation."""

    async def create(self, approval: ApprovalRequest) -> None: ...


class MemoryProvider(Protocol):
    """Optional. What is remembered about the user that bears on a message.

    The core hands the result to the capability provider (``AgentRun.memories``), which writes the instructions.
    """

    async def recall(self, query: str, *, user_id: str, tenant_id: str, limit: int) -> list[Any]: ...


class RunEvents(Protocol):
    """Optional. The trace of a run: its events and the spans of its model and capability calls."""

    async def push(self, trace_id: str, *, kind: str, status: str, **fields: Any) -> None: ...

    async def start_span(self, trace_id: str, **fields: Any) -> None: ...

    async def finish_span(self, trace_id: str, span_id: str, status: str, **fields: Any) -> None: ...


class NoRunEvents:
    async def push(self, trace_id: str, *, kind: str, status: str, **fields: Any) -> None:
        return None

    async def start_span(self, trace_id: str, **fields: Any) -> None:
        return None

    async def finish_span(self, trace_id: str, span_id: str, status: str, **fields: Any) -> None:
        return None


# Optional. Receives every model request and, again, its response.
ModelCallLog = Callable[[ModelCallRecord], Awaitable[None]]
