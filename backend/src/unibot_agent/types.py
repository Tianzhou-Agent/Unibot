"""Data the agent core exchanges with the services behind its ports."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

# Key of a model's thinking text in an AI message's ``additional_kwargs``.
REASONING_KEY = "reasoning_content"


def _utc_now() -> datetime:
    return datetime.now(UTC)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Usage(_Strict):
    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = False


class ModelCallRecord(_Strict):
    """One request to a chat model and its response, as sent."""

    call_id: str
    trace_id: str | None = None
    span_id: str | None = None
    context_type: str | None = None
    context_id: str | None = None
    endpoint: str
    model: str
    status: Literal["running", "completed", "failed"] = "running"
    request: dict[str, Any]
    response: dict[str, Any] | None = None
    duration_ms: float | None = None
    first_token_at: datetime | None = None
    ttft_ms: float | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=_utc_now)
    completed_at: datetime | None = None


@dataclass(slots=True)
class AgentConfig:
    # Model calls one turn (or one continuation of it) may make.
    max_agent_iterations: int = 60
    context_compression_enabled: bool = True
    # Share of the request's input budget at which earlier turns are summarized.
    context_compression_threshold_ratio: float = 0.75
    context_compression_keep_recent_turns: int = 4
    context_compression_min_messages: int = 8


@dataclass(slots=True)
class Message:
    """One message of a session's archive."""

    id: str
    role: str
    content: str = ""
    reasoning: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    # Files a user message attached, as ``unibot_agent.attachments`` shows them to the model.
    attachments: list[dict[str, Any]] = field(default_factory=list)
    # What capability calls produced for the user to see with this message; opaque to the core.
    artifacts: list[Any] = field(default_factory=list)
    trace_id: str | None = None


@dataclass(slots=True)
class Session:
    """The conversation a turn runs in, as its host loaded it."""

    id: str
    user_id: str
    tenant_id: str
    workspace_id: str | None = None
    # Ids of the messages already archived.
    message_ids: frozenset[str] = frozenset()
    # The host's own record of the session; the core never reads it.
    record: Any = None


@dataclass(slots=True)
class TurnRequest:
    message: str
    # Transient context of the user's screen: shown to the model with this message, never stored.
    ui_context: str | None = None
    # A capability the turn must use, and the scope it should start in; the capability provider reads both.
    capability: str | None = None
    scope_id: str | None = None


@dataclass(slots=True)
class ApprovalRequest:
    """A batch of capability calls paused until the user approves or denies it."""

    id: str
    conversation_id: str
    user_id: str
    tenant_id: str
    trace_id: str
    tool_calls: list[dict[str, Any]]
    capability_names: list[str]
    # Where the paused execution is kept (engine, thread and interrupt ids).
    runtime_ref: dict[str, Any] | None = None
    created_at: datetime = field(default_factory=_utc_now)
    # ``Capability.confirmation`` of the paused calls that have one, and the calls whose arguments may be edited.
    confirmations: list[Any] = field(default_factory=list)
    editable_call_ids: frozenset[str] = frozenset()


@dataclass(slots=True)
class TurnResult:
    conversation_id: str
    message_id: str | None
    content: str
    status: Literal["completed", "approval_required", "failed", "stopped"]
    trace_id: str
    iterations: int
    usage: Usage = field(default_factory=Usage)
    approval: ApprovalRequest | None = None
    artifacts: list[Any] = field(default_factory=list)
    # Values the capability provider set on the run (``AgentRun.values``).
    values: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelHandle:
    """The chat model serving a run, and what the core must know about it."""

    # None when no model is configured; the run then fails once it needs the model.
    model: BaseChatModel | None
    name: str
    endpoint: str
    context_window_tokens: int
    max_output_tokens: int | None = None
    # The error to raise instead of a provider exception, or None when the exception is not the provider's.
    map_error: Callable[[Exception], Exception | None] = lambda exc: None
    # Whether the provider refused the request because it named the tool to call.
    rejects_tool_choice: Callable[[BaseException], bool] = lambda exc: False
