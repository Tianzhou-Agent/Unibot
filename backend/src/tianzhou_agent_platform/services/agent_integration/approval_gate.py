"""Approval gate middleware: pause risky tool calls until approved.

Uses native ``after_model`` jump-to-end so no sibling tool executes and no
extra model turn is generated. Confirm/resume supplies ``approved_call_ids``.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage


class ApprovalRequired(Exception):
    """Internal signal: a risky tool needs user approval before execution."""

    def __init__(self, tool_calls: list[dict[str, Any]], capability_names: list[str]) -> None:
        super().__init__("approval_required")
        self.tool_calls = tool_calls
        self.capability_names = capability_names


def _last_ai(state: Any) -> Any:
    messages = (state or {}).get("messages") or []
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.tool_calls:
            return message
        if isinstance(message, dict) and message.get("tool_calls"):
            return message
    return None


def _tool_calls(message: Any) -> list[dict[str, Any]]:
    if message is None:
        return []
    calls = getattr(message, "tool_calls", None)
    if calls:
        return [dict(c) for c in calls]
    if isinstance(message, dict):
        return list(message.get("tool_calls") or [])
    return []


class ApprovalGateMiddleware(AgentMiddleware):
    """Whole-batch pause when any risky call is not approved.

    ``context['approved_call_ids']`` is set by confirm/resume. When a risky
    call is not approved, jump to end before any tool executes (product rule:
    a valid risky call pauses the whole batch).
    """

    def __init__(self, risky_function_names: set[str] | None = None) -> None:
        self.risky_function_names = risky_function_names or set()
        self.last_rejected: list[dict[str, Any]] = []
        self.pending_batch: list[dict[str, Any]] = []
        self.invalid_calls: list[dict[str, Any]] = []

    def set_risky(self, names: set[str]) -> None:
        self.risky_function_names = set(names)

    def _rejected(self, state: Any, runtime: Any) -> list[dict[str, Any]]:
        last = _last_ai(state)
        calls = _tool_calls(last)
        if not calls:
            return []
        # Only pending calls (no ToolMessage yet) can require approval.
        messages = (state or {}).get("messages") or []
        executed: set[str] = set()
        for message in messages:
            if isinstance(message, ToolMessage):
                executed.add(str(message.tool_call_id or ""))
            elif isinstance(message, dict) and message.get("role") == "tool":
                executed.add(str(message.get("tool_call_id") or ""))
        pending = [c for c in calls if str(c.get("id") or "") not in executed]
        if not pending:
            return []
        context = {}
        if runtime is not None:
            context = getattr(runtime, "context", None) or {}
        approved = set(context.get("approved_call_ids") or ())
        validators: dict[str, Any] = context.get("arg_validators") or {}
        rejected: list[dict[str, Any]] = []
        for c in pending:
            name = str(c.get("name"))
            if name not in self.risky_function_names:
                continue
            if str(c.get("id")) in approved:
                continue
            # Validate BEFORE requesting approval (A5): invalid args never prompt.
            validator = validators.get(name)
            if validator is not None:
                try:
                    validator(c.get("args") or {})
                except Exception:  # noqa: BLE001
                    self.invalid_calls.append(dict(c))
                    continue
            rejected.append(c)
        return rejected

    @hook_config(can_jump_to=["end"])
    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        rejected = self._rejected(state, runtime)
        if not rejected:
            return None
        self.last_rejected = rejected
        self.pending_batch = _tool_calls(_last_ai(state))
        return {"jump_to": "end"}

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        return await handler(request)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        return handler(request)
