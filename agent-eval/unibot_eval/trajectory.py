"""Normalise one backend run (REST response + OTel-derived trace + LLM call log) into an observation.

The backend records every run as a ``TraceRecord`` with business ``events`` (``tool.requested``,
``builtin.completed``, ``context.compacted``, ``approval.required`` ...) and OTel ``spans`` (kind ``agent``,
``model``, ``tool``, ``aina``, ``internal``). Capability calls are rebuilt from the events and enriched from the
spans (``logical_call_id`` == ``call_id``), so the scorers see name, arguments, result, status and duration of
every Tool / AINA / builtin call in order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

CAPABILITY_KINDS = ("tool", "aina", "builtin")
CALL_PHASES = ("requested", "completed", "failed")
SUMMARY_PREFIX = "[CONTEXT SUMMARY"


@dataclass
class ToolCall:
    name: str
    kind: str
    call_id: str | None = None
    function: str | None = None
    arguments: Any = None
    result: Any = None
    status: str = "requested"
    error: Any = None
    duration_ms: float | None = None

    def is_named(self, name: str) -> bool:
        return name in {self.name, self.function}

    def signature(self) -> str:
        return f"{self.name}:{json.dumps(self.arguments, sort_keys=True, ensure_ascii=False, default=str)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "call_id": self.call_id,
            "function": self.function,
            "arguments": self.arguments,
            "status": self.status,
            "error": self.error,
            "duration_ms": self.duration_ms,
        }


@dataclass
class TurnObservation:
    index: int
    actor: str
    user_id: str
    action: str  # "chat" | "confirm_approval" | "deny_approval"
    input: str | None
    response: dict[str, Any]
    trace: dict[str, Any] = field(default_factory=dict)
    llm_calls: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: float | None = None
    ttft_ms: float | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def status(self) -> str:
        return str(self.response.get("status") or self.trace.get("status") or "unknown")

    @property
    def content(self) -> str:
        return str(self.response.get("content") or "").strip()

    @property
    def trace_id(self) -> str | None:
        return self.response.get("trace_id") or self.trace.get("trace_id")

    @property
    def conversation_id(self) -> str | None:
        return self.response.get("conversation_id")

    @property
    def iterations(self) -> int | None:
        return self.response.get("iterations")

    @property
    def widgets(self) -> list[dict[str, Any]]:
        return list(self.response.get("widgets") or [])

    @property
    def approval(self) -> dict[str, Any] | None:
        return self.response.get("approval")

    @property
    def input_tokens(self) -> int:
        return int((self.response.get("usage") or {}).get("input_tokens") or 0)

    @property
    def output_tokens(self) -> int:
        return int((self.response.get("usage") or {}).get("output_tokens") or 0)

    @property
    def event_kinds(self) -> list[str]:
        return [str(event.get("kind")) for event in self.trace.get("events", [])]

    @property
    def model_call_count(self) -> int:
        spans = [span for span in self.trace.get("spans", []) if span.get("kind") == "model"]
        if spans:
            return len(spans)
        return len(self.agent_llm_calls()) or int(self.iterations or 0)

    def agent_llm_calls(self) -> list[dict[str, Any]]:
        """LLM calls made by the agent loop (compression / routing helper calls excluded)."""
        return [call for call in self.llm_calls if call.get("context_type") not in {"compression"}]

    def model_requests(self) -> list[list[dict[str, Any]]]:
        """The message lists the model actually received for this turn, one per agent LLM call."""
        requests = [_messages_of(call.get("request")) for call in self.agent_llm_calls()]
        requests = [messages for messages in requests if messages]
        if requests:
            return requests
        # Fallback: model span previews (may be truncated/redacted by the backend).
        return [
            messages
            for span in self.trace.get("spans", [])
            if span.get("kind") == "model" and (messages := _messages_of(span.get("input")))
        ]

    def prompt_text(self) -> str:
        return "\n".join(
            _content_text(message.get("content")) for messages in self.model_requests() for message in messages
        )

    def summary(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "actor": self.actor,
            "action": self.action,
            "input": self.input,
            "status": self.status,
            "content": self.content[:2000],
            "trace_id": self.trace_id,
            "conversation_id": self.conversation_id,
            "iterations": self.iterations,
            "latency_ms": self.latency_ms,
            "ttft_ms": self.ttft_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_calls": [call.to_dict() for call in self.tool_calls],
            "widgets": [widget.get("kind") for widget in self.widgets],
            "events": self.event_kinds,
        }


def extract_tool_calls(trace: dict[str, Any]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    by_id: dict[str, ToolCall] = {}
    for event in trace.get("events", []):
        prefix, _, phase = str(event.get("kind", "")).partition(".")
        if prefix not in CAPABILITY_KINDS or phase not in CALL_PHASES:
            continue
        details = event.get("details") or {}
        call_id = details.get("call_id")
        name = event.get("target_id") or details.get("function") or details.get("name")
        call = by_id.get(call_id) if call_id else _open_call(calls, name) if phase != "requested" else None
        if call is None:
            call = ToolCall(name=str(name or "unknown"), kind=prefix, call_id=call_id)
            calls.append(call)
            if call_id:
                by_id[call_id] = call
        if event.get("target_id"):
            call.name = str(event["target_id"])
        call.function = call.function or details.get("function")
        if "arguments" in details and call.arguments is None:
            call.arguments = details["arguments"]
        if phase != "requested":
            call.status = phase
            call.result = details.get("result", call.result)
            call.error = details.get("error", call.error)
            call.duration_ms = event.get("duration_ms", call.duration_ms)

    for span in trace.get("spans", []):
        if span.get("kind") not in {"tool", "aina"}:
            continue
        call = by_id.get(span.get("logical_call_id") or "")
        if call is None:
            if by_id or calls:
                continue  # events are authoritative when present
            call = ToolCall(name=str(span.get("target_id") or span.get("name")), kind=str(span["kind"]))
            calls.append(call)
        if call.arguments is None:
            call.arguments = span.get("input") or (span.get("attributes") or {}).get("arguments")
        if call.result is None:
            call.result = span.get("output")
        if call.duration_ms is None:
            call.duration_ms = span.get("duration_ms")
        if call.status == "requested" and span.get("status") in {"completed", "failed"}:
            call.status = span["status"]

    if trace.get("status") == "approval_required":
        for call in calls:
            if call.status == "requested":
                call.status = "pending_approval"
    return calls


def _open_call(calls: list[ToolCall], name: Any) -> ToolCall | None:
    for call in reversed(calls):
        if call.status == "requested" and (name is None or call.is_named(str(name))):
            return call
    return None


def _messages_of(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("messages")
    if isinstance(value, list):
        return [message for message in value if isinstance(message, dict)]
    return []


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(part.get("text", "")) if isinstance(part, dict) else str(part) for part in content
        )
    return "" if content is None else json.dumps(content, ensure_ascii=False, default=str)
