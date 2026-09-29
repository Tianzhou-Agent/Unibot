"""Ordered tool batches and run-scoped signature deduplication.

The native tool node runs the calls of one model response concurrently. ``OrderedBatchMiddleware`` makes each call
wait for its predecessor in the response, skips a repeated name/argument combination within the batch, and keeps a
run-scoped execution ledger: an identical logical call executes again only after a retryable failure, and never
more than ``MAX_LOGICAL_ATTEMPTS`` times in one invocation.

All state lives on the middleware instance, so build one instance per agent invocation (a turn or an approval
continuation); concurrent invocations never share batches or attempt budgets.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

MAX_LOGICAL_ATTEMPTS = 3


def tool_signature(name: str, args: Any) -> str:
    payload = json.dumps({"name": name, "args": args}, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class RunExecutionLedger:
    """Logical-call attempts of one invocation (survives model iterations)."""

    attempts: dict[str, int] = field(default_factory=dict)
    retryable: set[str] = field(default_factory=set)

    def may_attempt(self, name: str, args: Any) -> bool:
        signature = tool_signature(name, args)
        attempts = self.attempts.get(signature, 0)
        return attempts == 0 or (signature in self.retryable and attempts < MAX_LOGICAL_ATTEMPTS)

    def bump_attempt(self, name: str, args: Any) -> int:
        signature = tool_signature(name, args)
        self.attempts[signature] = self.attempts.get(signature, 0) + 1
        return self.attempts[signature]

    def record_outcome(self, name: str, args: Any, *, retryable_failure: bool) -> None:
        signature = tool_signature(name, args)
        if retryable_failure:
            self.retryable.add(signature)
        else:
            self.retryable.discard(signature)

    def attempt_count(self, name: str, args: Any) -> int:
        return self.attempts.get(tool_signature(name, args), 0)


class BatchDeduper:
    """Same-batch logical-call deduplication, outside native retries."""

    def __init__(self) -> None:
        self._seen: set[str] = set()

    def claim(self, name: str, args: Any) -> bool:
        signature = tool_signature(name, args)
        if signature in self._seen:
            return False
        self._seen.add(signature)
        return True


@dataclass
class BatchCoordinator:
    """Order and dedup state of the calls of one model response."""

    order: list[str] = field(default_factory=list)
    index: dict[str, int] = field(default_factory=dict)
    done_events: dict[str, asyncio.Event] = field(default_factory=dict)
    started: set[str] = field(default_factory=set)
    deduper: BatchDeduper = field(default_factory=BatchDeduper)

    @classmethod
    def from_tool_calls(cls, tool_calls: list[dict[str, Any]]) -> BatchCoordinator:
        coordinator = cls()
        for position, call in enumerate(tool_calls):
            call_id = _call_id(call, position)
            coordinator.order.append(call_id)
            coordinator.index[call_id] = position
            coordinator.done_events[call_id] = asyncio.Event()
        return coordinator

    def mark_done(self, call_id: str) -> None:
        event = self.done_events.get(call_id)
        if event is not None:
            event.set()

    async def wait_predecessor(self, call_id: str, *, timeout: float | None = None) -> bool:
        """Wait for the previous call of the batch. False on timeout (the caller must not execute)."""
        position = self.index.get(call_id)
        if not position:
            return True
        event = self.done_events[self.order[position - 1]]
        try:
            if timeout is None:
                await event.wait()
            else:
                await asyncio.wait_for(event.wait(), timeout=timeout)
            return True
        except TimeoutError:
            return False


class OrderedBatchMiddleware(AgentMiddleware):
    """Preserve tool-batch order and run-scoped signature deduplication.

    Compose as the outermost tool-call wrapper so logical-call dedup happens outside native retries (retry
    attempts are not mistaken for duplicates).
    """

    def __init__(
        self,
        *,
        enforce_order: bool = True,
        enforce_dedup: bool = True,
        order_timeout: float | None = 30.0,
    ) -> None:
        super().__init__()
        self.enforce_order = enforce_order
        self.enforce_dedup = enforce_dedup
        self.order_timeout = order_timeout
        self.ledger = RunExecutionLedger()
        self._batches: dict[frozenset[str], BatchCoordinator] = {}

    def _batch(self, tool_calls: list[dict[str, Any]], *, fresh: bool = False) -> BatchCoordinator:
        key = frozenset(_call_id(call, position) for position, call in enumerate(tool_calls))
        coordinator = None if fresh else self._batches.get(key)
        if coordinator is None:
            coordinator = self._batches[key] = BatchCoordinator.from_tool_calls(tool_calls)
        return coordinator

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        tool_calls = _last_ai_tool_calls(state)
        if tool_calls:
            # A new model response starts a new batch, even if a model reuses call ids.
            self._batch(tool_calls, fresh=True)
        return None

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.after_model(state, runtime)

    async def awrap_tool_call(self, request: Any, handler: Any) -> Any:
        tool_call = request.tool_call
        call_id = str(tool_call.get("id") or "")
        name = str(tool_call.get("name") or "")
        args = tool_call.get("args") or {}
        coordinator = self._batch(_last_ai_tool_calls(request.state) or [tool_call])

        def error(code: str, message: str) -> ToolMessage:
            return ToolMessage(
                content=json.dumps(
                    {"error": {"code": code, "message": message, "retryable": False, "source": "tool"}},
                    ensure_ascii=False,
                ),
                tool_call_id=call_id,
                name=name,
                status="error",
            )

        try:
            if self.enforce_order and not await coordinator.wait_predecessor(call_id, timeout=self.order_timeout):
                # A later call never runs before (or without) the call ahead of it.
                return error("DEPENDENCY_FAILED", "Previous tool in the batch did not finish in time")
            if self.enforce_dedup:
                if call_id in coordinator.started:
                    return error("CONFLICT", "Duplicate tool call in the same batch was skipped")
                coordinator.started.add(call_id)
                if not coordinator.deduper.claim(name, args):
                    return error("CONFLICT", "Duplicate tool/argument combination already submitted in this batch")
                if not self.ledger.may_attempt(name, args):
                    return error("CONFLICT", "The same capability call was already attempted in this run.")
            self.ledger.bump_attempt(name, args)
            result = await handler(request)
            self.ledger.record_outcome(name, args, retryable_failure=_is_retryable_failure(result))
            return result
        finally:
            coordinator.mark_done(call_id)


def _call_id(call: dict[str, Any], position: int) -> str:
    return str(call.get("id") or f"pos_{position}")


def _last_ai_tool_calls(state: Any) -> list[dict[str, Any]]:
    for message in reversed((state or {}).get("messages") or []):
        if isinstance(message, AIMessage):
            return [dict(call) for call in message.tool_calls]
    return []


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
