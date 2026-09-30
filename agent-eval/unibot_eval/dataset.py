"""YAML dataset schema.

A case is a scripted conversation of one or more turns. Every turn may declare expectations that are checked
deterministically (tools, arguments, output, widgets, prompt contents, trace events, budgets) and, optionally,
by an LLM judge. ``{{marker}}`` style placeholders are replaced with fresh random values on every attempt so
repeated runs never hit a cache or leak state between attempts.
"""

from __future__ import annotations

import os
import re
import secrets
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

TrajectoryMode = Literal["strict", "unordered", "subset", "superset"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CallCount(_Strict):
    min: int | None = None
    max: int | None = None

    @model_validator(mode="before")
    @classmethod
    def _exact(cls, data: Any) -> Any:
        return {"min": data, "max": data} if isinstance(data, int) else data


class ToolExpectation(_Strict):
    # Trajectory match against the ordered list of capability names the agent called.
    #   strict    same calls, same order            unordered  same calls, any order
    #   superset  at least these calls (extras ok)  subset     only calls from this list (may skip some)
    expected: list[str] | None = None
    mode: TrajectoryMode = "strict"
    forbidden: list[str] = Field(default_factory=list)
    none: bool = False  # the agent must not call any capability
    allow_failed: bool = False  # tolerate failed capability calls (robustness cases)
    # Long runs: how often a capability was called ({tool: 5} is exactly 5, or {tool: {min: 1, max: 3}}), and
    # ordering constraints: each [a, b] pair requires the first call of a to come before the first call of b.
    counts: dict[str, CallCount] = Field(default_factory=dict)
    order: list[tuple[str, str]] = Field(default_factory=list)


class ArgExpectation(_Strict):
    tool: str
    args: dict[str, Any]
    match: Literal["subset", "exact"] = "subset"
    case_insensitive: bool = True


class OutputExpectation(_Strict):
    equals: str | None = None
    contains: list[str] = Field(default_factory=list)
    contains_any: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)
    regex: str | None = None
    case_insensitive: bool = True


class WidgetExpectation(_Strict):
    kind: str
    fields: dict[str, Any] = Field(default_factory=dict)


class PromptExpectation(_Strict):
    """Checks on what the model actually received (from ``/llm-calls``): the core context-management signal."""

    contains: list[str] = Field(default_factory=list)
    not_contains: list[str] = Field(default_factory=list)
    has_summary: bool | None = None
    max_messages: int | None = None


class TraceExpectation(_Strict):
    events_include: list[str] = Field(default_factory=list)
    events_exclude: list[str] = Field(default_factory=list)


class LlmCallsExpectation(_Strict):
    """Checks on the agent's recorded model calls (``/llm-calls``)."""

    # Model every agent call was served by, as reported by the provider (catches a selection being ignored).
    served_model: str | None = None


class BodyCheck(OutputExpectation):
    """A check on one value of an API response, e.g. the content of a document after a long task."""

    field: str | None = None  # dotted path into the JSON response; the whole body when omitted


class HttpStep(_Strict):
    """A setup request against the backend API (e.g. create and select a model provider), or a state check."""

    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"] = "POST"
    path: str
    body: dict[str, Any] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    # Save values from the JSON response for later steps: variable name -> dotted path (e.g. "models.0.id").
    save: dict[str, str] = Field(default_factory=dict)
    expect_status: int | None = None  # exact HTTP status required (default: any 2xx), e.g. 404 after a delete
    verify: list[BodyCheck] = Field(default_factory=list)


class PollStep(_Strict):
    """Poll a GET endpoint until a background job settles (e.g. a document edit task)."""

    path: str
    params: dict[str, Any] = Field(default_factory=dict)
    field: str  # dotted path of the watched value
    until: list[Any]  # stop polling once the value is one of these
    equals: Any = None  # value required to pass (default: the first ``until`` value)
    timeout_s: float = 180.0
    interval_s: float = 2.0


class Budget(_Strict):
    max_iterations: int | None = None
    max_latency_ms: float | None = None
    max_ttft_ms: float | None = None
    max_total_tokens: int | None = None
    max_tool_calls: int | None = None
    max_duplicate_calls: int | None = None


class JudgeExpectation(_Strict):
    rubric: Literal["correctness", "groundedness", "safety"] = "correctness"
    criteria: str
    reference: str | None = None
    threshold: float = 0.7


class StopTrigger(_Strict):
    """When the harness stops a running turn (``POST /conversations/{id}/stop``), like a user pressing stop."""

    tool_calls: int | None = None  # after this many capability calls completed (from the SSE stream)
    seconds: float | None = None  # after this many seconds

    @model_validator(mode="after")
    def _one_trigger(self) -> "StopTrigger":
        if (self.tool_calls is None) == (self.seconds is None):
            raise ValueError("stop_after needs exactly one of 'tool_calls' or 'seconds'")
        return self


class Expectation(_Strict):
    status: Literal["completed", "approval_required", "failed", "stopped"] | None = "completed"
    tools: ToolExpectation | None = None
    tool_args: list[ArgExpectation] = Field(default_factory=list)
    output: OutputExpectation | None = None
    widgets: list[WidgetExpectation] = Field(default_factory=list)
    prompt: PromptExpectation | None = None
    trace: TraceExpectation | None = None
    budget: Budget | None = None
    llm_calls: LlmCallsExpectation | None = None
    # The turn is expected to end with a backend error whose code/message contains this text (e.g. a provider
    # 401 proving that a deliberately broken selected provider is actually called).
    error: str | None = None
    judge: list[JudgeExpectation] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _single_judge(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("judge"), dict):
            data = {**data, "judge": [data["judge"]]}
        return data


class Turn(_Strict):
    user: str | None = None
    action: Literal["chat", "confirm_approval", "deny_approval", "resume", "http", "poll"] = "chat"
    actor: str = "a"  # label; each label maps to its own isolated user id and conversation
    new_conversation: bool = False
    capability: str | None = None
    preferred_aina_id: str | None = None
    ui_context: str | None = None
    request: HttpStep | None = None  # action: http
    poll: PollStep | None = None  # action: poll
    stop_after: StopTrigger | None = None  # chat: stop the turn while it runs
    # confirm_approval: keep confirming while the continuation pauses for another approval, up to this many times.
    max_confirmations: int = Field(default=1, ge=1)
    timeout_s: float | None = None  # per-request timeout for long turns (default: UNIBOT_EVAL_TIMEOUT)
    expect: Expectation = Field(default_factory=Expectation)

    @model_validator(mode="after")
    def _message_required(self) -> "Turn":
        if self.action == "chat" and not self.user:
            raise ValueError("chat turns need a 'user' message")
        if self.stop_after is not None and self.action != "chat":
            raise ValueError("stop_after only applies to chat turns")
        if self.action == "http" and self.request is None:
            raise ValueError("http turns need a 'request'")
        if self.action == "poll" and self.poll is None:
            raise ValueError("poll turns need a 'poll'")
        if self.action in {"http", "poll"} and "status" not in self.expect.model_fields_set:
            self.expect.status = None  # API steps are not agent runs
        if self.action == "deny_approval" and "status" not in self.expect.model_fields_set:
            # A denial returns the closed approval record, not an agent response.
            self.expect.status = None
        return self


class Case(_Strict):
    id: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    repeats: int | None = None
    requires: list[str] = Field(default_factory=list)  # e.g. "compression": only runs when explicitly enabled
    vars: dict[str, str] = Field(default_factory=dict)  # extra static placeholders
    turns: list[Turn]
    # Checks over the whole attempt: every agent turn merged into one (all tool calls in order, summed tokens,
    # latency and iterations). Catches long-run failures no single turn shows, e.g. the same work done twice.
    final: Expectation | None = None
    source: str | None = None

    @model_validator(mode="after")
    def _final_defaults(self) -> "Case":
        if self.final is not None:
            if self.final.judge:
                raise ValueError("final checks do not support 'judge'; put the judge on a turn")
            if "status" not in self.final.model_fields_set:
                self.final.status = None
        return self

    def render(self, variables: dict[str, str]) -> "Case":
        """Return a copy with every ``{{name}}`` placeholder replaced."""
        return Case.model_validate(_render(self.model_dump(), variables))


def fresh_vars(case: Case) -> dict[str, str]:
    token = secrets.token_hex(4).upper()
    return {
        "marker": f"MK-{token}",
        "marker2": f"MK2-{secrets.token_hex(4).upper()}",
        "secret": f"SECRET-{secrets.token_hex(5).upper()}",
        "number": str(secrets.randbelow(9000) + 1000),
        **case.vars,
    }


def load_cases(paths: list[str | Path]) -> list[Case]:
    cases: list[Case] = []
    for root in paths:
        root = Path(root)
        files = sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.yml")) if root.is_dir() else [root]
        for file in files:
            documents = yaml.safe_load(file.read_text(encoding="utf-8")) or []
            for raw in documents.get("cases", []) if isinstance(documents, dict) else documents:
                case = Case.model_validate(raw)
                case.source = str(file)
                cases.append(case)
    ids = [case.id for case in cases]
    duplicates = {case_id for case_id in ids if ids.count(case_id) > 1}
    if duplicates:
        raise ValueError(f"duplicate case ids: {sorted(duplicates)}")
    return cases


def select_cases(
    cases: list[Case],
    *,
    tags: list[str] | None = None,
    ids: list[str] | None = None,
    enable: list[str] | None = None,
) -> list[Case]:
    enabled = set(enable or [])
    selected = []
    for case in cases:
        if ids and not any(re.fullmatch(pattern.replace("*", ".*"), case.id) for pattern in ids):
            continue
        if tags and not set(tags) & set(case.tags):
            continue
        if not set(case.requires) <= enabled:
            continue
        selected.append(case)
    return selected


_PLACEHOLDER = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _render(value: Any, variables: dict[str, str]) -> Any:
    if isinstance(value, str):
        return _PLACEHOLDER.sub(lambda match: variables.get(match.group(1), match.group(0)), value)
    if isinstance(value, list):
        return [_render(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: _render(item, variables) for key, item in value.items()}
    return value


_RUNTIME_PLACEHOLDER = re.compile(r"\{\{\s*([\w.|]+)\s*\}\}")


def render_runtime(value: Any, variables: dict[str, str]) -> Any:
    """Resolve placeholders known only while an attempt runs: ``{{user_id}}``, values saved by earlier steps,
    and ``{{env.NAME}}`` / ``{{env.A|B}}`` (first set environment variable, e.g. provider credentials)."""

    def resolve(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.startswith("env."):
            # {{env.A|B|C}}: the first of these environment variables that is set
            for key in name[4:].split("|"):
                if os.environ.get(key):
                    return os.environ[key]
            return match.group(0)
        return variables.get(name, match.group(0))

    if isinstance(value, str):
        return _RUNTIME_PLACEHOLDER.sub(resolve, value)
    if isinstance(value, list):
        return [render_runtime(item, variables) for item in value]
    if isinstance(value, dict):
        return {key: render_runtime(item, variables) for key, item in value.items()}
    return value


def dig(data: Any, path: str) -> Any:
    """Value at a dotted path (``items.0.status``); None when absent."""
    for part in path.split("."):
        if isinstance(data, list) and part.isdigit() and int(part) < len(data):
            data = data[int(part)]
        elif isinstance(data, dict):
            data = data.get(part)
        else:
            return None
    return data
