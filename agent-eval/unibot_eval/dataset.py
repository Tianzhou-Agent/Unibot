"""YAML dataset schema.

A case is a scripted conversation of one or more turns. Every turn may declare expectations that are checked
deterministically (tools, arguments, output, widgets, prompt contents, trace events, budgets) and, optionally,
by an LLM judge. ``{{marker}}`` style placeholders are replaced with fresh random values on every attempt so
repeated runs never hit a cache or leak state between attempts.
"""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

TrajectoryMode = Literal["strict", "unordered", "subset", "superset"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolExpectation(_Strict):
    # Trajectory match against the ordered list of capability names the agent called.
    #   strict    same calls, same order            unordered  same calls, any order
    #   superset  at least these calls (extras ok)  subset     only calls from this list (may skip some)
    expected: list[str] | None = None
    mode: TrajectoryMode = "strict"
    forbidden: list[str] = Field(default_factory=list)
    none: bool = False  # the agent must not call any capability
    allow_failed: bool = False  # tolerate failed capability calls (robustness cases)


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


class Expectation(_Strict):
    status: Literal["completed", "approval_required", "failed"] | None = "completed"
    tools: ToolExpectation | None = None
    tool_args: list[ArgExpectation] = Field(default_factory=list)
    output: OutputExpectation | None = None
    widgets: list[WidgetExpectation] = Field(default_factory=list)
    prompt: PromptExpectation | None = None
    trace: TraceExpectation | None = None
    budget: Budget | None = None
    judge: list[JudgeExpectation] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _single_judge(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("judge"), dict):
            data = {**data, "judge": [data["judge"]]}
        return data


class Turn(_Strict):
    user: str | None = None
    action: Literal["chat", "confirm_approval", "deny_approval"] = "chat"
    actor: str = "a"  # label; each label maps to its own isolated user id and conversation
    new_conversation: bool = False
    capability: str | None = None
    preferred_aina_id: str | None = None
    ui_context: str | None = None
    expect: Expectation = Field(default_factory=Expectation)

    @model_validator(mode="after")
    def _message_required(self) -> "Turn":
        if self.action == "chat" and not self.user:
            raise ValueError("chat turns need a 'user' message")
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
    source: str | None = None

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
