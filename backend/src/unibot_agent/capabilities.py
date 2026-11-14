"""Capabilities: the functions the model may call, as the agent core sees them."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Capability:
    """One function the model may call; the provider that lists it also executes it.

    The core gives ``kind`` no meaning: it names the capability's trace events (``<kind>.requested`` ...). A provider
    describes how its capabilities behave by subclassing and overriding the properties and methods below.
    """

    kind: str
    capability_id: str
    function_name: str
    display_name: str
    description: str
    input_schema: dict[str, Any]
    requires_confirmation: bool
    # The provider's own record of the capability; the core never reads it.
    value: Any
    # The scope (group of capabilities) this one belongs to, if any.
    owner_id: str | None = None
    # Confirmation is asked only until one call of this capability has completed in the conversation.
    confirm_once: bool = False

    def llm_definition(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.function_name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }

    @property
    def scope_entry(self) -> bool:
        """Stands for a scope rather than one operation; the model may call one such capability per response."""
        return False

    @property
    def activates_scope(self) -> bool:
        """Calling it only switches the run to its scope's capabilities and instructions."""
        return False

    @property
    def retry_safe(self) -> bool:
        """Has no side effects, so a transient failure may be retried (automatically or by the model)."""
        return False

    @property
    def retries(self) -> int:
        """Automatic retries of a transient failure."""
        return 0

    @property
    def repeatable(self) -> bool:
        """Identical calls in one run are all executed instead of being deduplicated."""
        return False

    @property
    def version(self) -> str | None:
        return None

    @property
    def editable(self) -> bool:
        """The user may change a paused call's arguments when approving it."""
        return False

    def confirmation(self, call_id: str, arguments: dict[str, Any]) -> Any | None:
        """What the user is shown when asked to approve this call, beyond its name (opaque to the core)."""
        return None

    def check_edit(self, arguments: dict[str, Any]) -> None:
        """Raise when arguments the user edited are not acceptable (they already match ``input_schema``)."""


@dataclass(slots=True)
class Recovery:
    """How the model can bring a capability outside the visible scope back into it."""

    capability: Capability
    message: str
    instruction: str
    details: dict[str, Any]


@dataclass(slots=True)
class CapabilityScope:
    """What the next model requests may call, and the instructions that go with it."""

    visible: dict[str, Capability]
    system_prompt: str
    # Every capability reachable in the run. All are registered as tools; ``visible`` decides which are advertised.
    registered: dict[str, Capability] = field(default_factory=dict)
    id: str | None = None
    # Function the first model request must call.
    forced_function: str | None = None
    # Recorded on the run's ``capability.discovery`` trace event.
    discovery: dict[str, Any] = field(default_factory=dict)
    recover: Callable[[str], Recovery | None] | None = None


def tool_error_payload(
    code: str,
    message: str,
    *,
    retryable: bool = False,
    recovery: Recovery | None = None,
) -> dict[str, Any]:
    """Model-visible error envelope of a capability call that did not complete."""
    instruction = "The capability did not complete. Do not claim success; report the failure."
    if retryable:
        instruction = "The read-only capability failed transiently. You may retry the same call; do not claim success."
    if recovery is not None:
        instruction = recovery.instruction
    payload: dict[str, Any] = {
        "error": {"code": code, "message": message, "retryable": retryable},
        "instruction": instruction,
    }
    if recovery is not None:
        payload["recovery"] = recovery.details
    return payload
