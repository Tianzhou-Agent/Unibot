"""Signature-based tool deduplication and ordered batch policy.

Run-scoped execution ledger (plan §6.3 / acceptance A2): successful logical
calls and attempt budgets live for one business run including approval resume.
Batch coordinator is isolated per run_id (acceptance A6).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware


def tool_signature(name: str, args: Any) -> str:
    payload = json.dumps({"name": name, "args": args}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


MAX_LOGICAL_ATTEMPTS = 3


@dataclass
class RunExecutionLedger:
    """Cross-batch execution record for one run (survives model iterations).

    An identical logical call (same name and arguments) executes once per run. It may run again only when its
    last attempt failed with a retryable error, and never more than ``MAX_LOGICAL_ATTEMPTS`` times in total, so
    neither successful side effects nor non-retryable failures are replayed by a repeating model.
    """

    attempts: dict[str, int] = field(default_factory=dict)
    retryable: set[str] = field(default_factory=set)
    activated_scope: bool = False

    def may_attempt(self, name: str, args: Any) -> bool:
        sig = tool_signature(name, args)
        attempts = self.attempts.get(sig, 0)
        return attempts == 0 or (sig in self.retryable and attempts < MAX_LOGICAL_ATTEMPTS)

    def bump_attempt(self, name: str, args: Any) -> int:
        sig = tool_signature(name, args)
        self.attempts[sig] = self.attempts.get(sig, 0) + 1
        return self.attempts[sig]

    def record_outcome(self, name: str, args: Any, *, retryable_failure: bool) -> None:
        sig = tool_signature(name, args)
        if retryable_failure:
            self.retryable.add(sig)
        else:
            self.retryable.discard(sig)

    def attempt_count(self, name: str, args: Any) -> int:
        return self.attempts.get(tool_signature(name, args), 0)


class BatchDeduper:
    """Same-batch logical-call deduplication, outside native retries."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def claim(self, name: str, args: Any) -> bool:
        sig = tool_signature(name, args)
        if sig in self._seen:
            return False
        self._seen.add(sig)
        return True

    def reset(self) -> None:
        self._seen.clear()


@dataclass
class BatchCoordinator:
    """Per-run, per-batch ordering + dedup."""

    run_id: str
    order: list[str] = field(default_factory=list)
    index: dict[str, int] = field(default_factory=dict)
    done_events: dict[str, asyncio.Event] = field(default_factory=dict)
    started: set[str] = field(default_factory=set)
    deduper: BatchDeduper = field(default_factory=BatchDeduper)
    ledger: RunExecutionLedger = field(default_factory=RunExecutionLedger)

    @classmethod
    def from_tool_calls(cls, run_id: str, tool_calls: list[dict[str, Any]] | None) -> BatchCoordinator:
        coord = cls(run_id=run_id)
        for position, call in enumerate(tool_calls or []):
            call_id = str(call.get("id") or f"pos_{position}")
            coord.order.append(call_id)
            coord.index[call_id] = position
            coord.done_events[call_id] = asyncio.Event()
        return coord

    def predecessor(self, call_id: str) -> str | None:
        pos = self.index.get(call_id)
        if pos is None or pos == 0:
            return None
        return self.order[pos - 1]

    def mark_done(self, call_id: str) -> None:
        event = self.done_events.get(call_id)
        if event is not None:
            event.set()

    async def wait_predecessor(self, call_id: str, *, timeout: float | None = None) -> bool:
        """Wait for predecessor. Returns False on timeout (caller must abort)."""
        prev = self.predecessor(call_id)
        if prev is None:
            return True
        event = self.done_events.get(prev)
        if event is None:
            return True
        try:
            if timeout is None:
                await event.wait()
            else:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            return True
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return False


# Per-run state. Keyed by run_id (set at the start of each business run).
_run_ledger_var: ContextVar[RunExecutionLedger | None] = ContextVar("unibot_run_ledger", default=None)
_batch_var: ContextVar[BatchCoordinator | None] = ContextVar("unibot_tool_batch", default=None)
# Batch coordinators are keyed by (run_id, frozenset(call_ids)) and never shared across runs.
_batch_registry: dict[tuple[str, frozenset[str]], BatchCoordinator] = {}


def current_ledger() -> RunExecutionLedger | None:
    return _run_ledger_var.get()


def current_batch() -> BatchCoordinator | None:
    return _batch_var.get()


def begin_run_ledger(run_id: str) -> RunExecutionLedger:
    ledger = RunExecutionLedger()
    _run_ledger_var.set(ledger)
    return ledger


def end_run_ledger() -> None:
    _run_ledger_var.set(None)
    # Drop coordinators for finished runs to keep the registry bounded.
    for key in [k for k in list(_batch_registry)]:
        _batch_registry.pop(key, None)


def _current_run_id() -> str:
    ledger = _run_ledger_var.get()
    # Stable identity for the business run (not call-id set).
    return str(id(ledger)) if ledger is not None else "no-run"


def _batch_key(run_id: str, tool_calls: list[dict[str, Any]]) -> tuple[str, frozenset[str]]:
    call_ids = frozenset(str(c.get("id") or f"pos_{i}") for i, c in enumerate(tool_calls or []))
    return (run_id, call_ids)


def _last_ai_tool_calls(state: Any) -> list[dict[str, Any]]:
    messages = (state or {}).get("messages") or []
    for message in reversed(messages):
        tool_calls = getattr(message, "tool_calls", None)
        if tool_calls:
            return [dict(c) for c in tool_calls]
        if isinstance(message, dict) and message.get("tool_calls"):
            return list(message["tool_calls"])
    return []


def _get_or_create_batch(tool_calls: list[dict[str, Any]], *, force_new: bool = False) -> BatchCoordinator:
    run_id = _current_run_id()
    key = _batch_key(run_id, tool_calls)
    coord = None if force_new else _batch_registry.get(key)
    if coord is None or all(e.is_set() for e in coord.done_events.values()):
        coord = BatchCoordinator.from_tool_calls(run_id, tool_calls)
        ledger = _run_ledger_var.get()
        if ledger is not None:
            coord.ledger = ledger
        _batch_registry[key] = coord
    _batch_var.set(coord)
    return coord


def _is_retryable_failure(result: Any) -> bool:
    """A tool result whose error envelope marks it retryable (``{"error": {"retryable": true}}``)."""
    content = getattr(result, "content", None)
    if not isinstance(content, str):
        return False
    try:
        payload = json.loads(content)
    except ValueError:
        return False
    error = payload.get("error") if isinstance(payload, dict) else None
    return isinstance(error, dict) and error.get("retryable") is True


class OrderedBatchMiddleware(AgentMiddleware):
    """Preserve tool-batch order and run-scoped signature deduplication.

    Compose as the outermost tool-call wrapper so logical-call dedup happens
    outside native retries (retry attempts are not mistaken for duplicates).
    """

    def __init__(
        self,
        *,
        enforce_order: bool = True,
        enforce_dedup: bool = True,
        order_timeout: float | None = 30.0,
    ) -> None:
        self.enforce_order = enforce_order
        self.enforce_dedup = enforce_dedup
        self.order_timeout = order_timeout

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        tool_calls = _last_ai_tool_calls(state)
        if tool_calls:
            _get_or_create_batch(tool_calls, force_new=True)
        return None

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        self.after_model(state, runtime)
        return None

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        from langchain_core.messages import ToolMessage

        tool_call = request.tool_call
        call_id = str(tool_call.get("id") or "")
        name = str(tool_call.get("name") or "")
        args = tool_call.get("args") or {}

        state = request.state or {}
        tool_calls = _last_ai_tool_calls(state)
        if not tool_calls:
            tool_calls = [tool_call]
        coord = _get_or_create_batch(tool_calls)
        ledger = coord.ledger

        if self.enforce_order and call_id in coord.index:
            ok = await coord.wait_predecessor(call_id, timeout=self.order_timeout)
            if not ok:
                # Timeout/cancel must NOT execute this tool (A6).
                coord.mark_done(call_id)
                return ToolMessage(
                    content=json.dumps(
                        {
                            "error": {
                                "code": "DEPENDENCY_FAILED",
                                "message": "Previous tool in the batch did not finish in time",
                                "retryable": False,
                                "source": "tool",
                            }
                        },
                        ensure_ascii=False,
                    ),
                    tool_call_id=call_id,
                    name=name,
                    status="error",
                )

        def conflict(message: str) -> ToolMessage:
            return ToolMessage(
                content=json.dumps(
                    {"error": {"code": "CONFLICT", "message": message, "retryable": False, "source": "tool"}},
                    ensure_ascii=False,
                ),
                tool_call_id=call_id,
                name=name,
                status="error",
            )

        try:
            if self.enforce_dedup:
                if call_id in coord.started:
                    return conflict("Duplicate tool call in the same batch was skipped")
                if call_id:
                    coord.started.add(call_id)
                if not coord.deduper.claim(name, args):
                    return conflict("Duplicate tool/argument combination already submitted in this batch")
                # Run-scoped attempt budget (A2): identical name+args run again only after a retryable failure.
                if not ledger.may_attempt(name, args):
                    return conflict("The same capability call was already attempted in this run.")
            ledger.bump_attempt(name, args)
            result = await handler(request)
            ledger.record_outcome(name, args, retryable_failure=_is_retryable_failure(result))
            return result
        finally:
            if call_id:
                coord.mark_done(call_id)

    def wrap_tool_call(self, request: Any, handler: Any) -> Any:
        return handler(request)
