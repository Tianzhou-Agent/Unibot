"""Deterministic scorers. Cheap, repeatable and exact: they run on every attempt, before any LLM judge."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from unibot_eval.dataset import (
    ArgExpectation,
    Budget,
    Expectation,
    LlmCallsExpectation,
    OutputExpectation,
    PromptExpectation,
    ToolExpectation,
    TraceExpectation,
    WidgetExpectation,
)
from unibot_eval.trajectory import SUMMARY_PREFIX, TurnObservation

# Categories double as the rows of the report's "behaviour" table.
TOOL_SELECTION = "tool_selection"
TOOL_ARGUMENTS = "tool_arguments"
CONTEXT = "context"
OUTPUT = "output"
PERFORMANCE = "performance"
PROTOCOL = "protocol"  # status, widgets, approval, trace events


@dataclass
class CheckResult:
    name: str
    category: str
    passed: bool
    score: float
    detail: str = ""
    turn: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_checks(expect: Expectation, turn: TurnObservation) -> list[CheckResult]:
    results: list[CheckResult] = []
    if expect.status is not None:
        results.append(check_status(expect.status, turn))
    if expect.tools is not None:
        results.extend(check_tools(expect.tools, turn))
    results.extend(check_tool_args(spec, turn) for spec in expect.tool_args)
    if expect.output is not None:
        results.extend(check_output(expect.output, turn))
    results.extend(check_widget(spec, turn) for spec in expect.widgets)
    if expect.prompt is not None:
        results.extend(check_prompt(expect.prompt, turn))
    if expect.trace is not None:
        results.extend(check_trace(expect.trace, turn))
    if expect.budget is not None:
        results.extend(check_budget(expect.budget, turn))
    if expect.llm_calls is not None:
        results.extend(check_llm_calls(expect.llm_calls, turn))
    if expect.error is not None:
        error = str(turn.response.get("error") or "")
        results.append(
            _result(f"error:{expect.error}", PROTOCOL, expect.error in error, error or "the turn did not fail")
        )
    for result in results:
        result.turn = turn.index
    return results


# ---- protocol ------------------------------------------------------------------------------------------------


def check_status(expected: str, turn: TurnObservation) -> CheckResult:
    return _result("status", PROTOCOL, turn.status == expected, f"expected {expected}, got {turn.status}")


def check_widget(spec: WidgetExpectation, turn: TurnObservation) -> CheckResult:
    for widget in turn.widgets:
        if widget.get("kind") == spec.kind and _subset_match(spec.fields, widget, case_insensitive=False):
            return _result(f"widget:{spec.kind}", PROTOCOL, True, "found")
    kinds = [widget.get("kind") for widget in turn.widgets]
    return _result(f"widget:{spec.kind}", PROTOCOL, False, f"widgets returned: {kinds}")


def check_trace(spec: TraceExpectation, turn: TurnObservation) -> list[CheckResult]:
    kinds = set(turn.event_kinds)
    results = [
        _result(f"event:{kind}", PROTOCOL, kind in kinds, "present" if kind in kinds else "missing")
        for kind in spec.events_include
    ]
    results += [
        _result(f"no_event:{kind}", PROTOCOL, kind not in kinds, "absent" if kind not in kinds else "present")
        for kind in spec.events_exclude
    ]
    return results


# ---- tool use ------------------------------------------------------------------------------------------------


def check_tools(spec: ToolExpectation, turn: TurnObservation) -> list[CheckResult]:
    called = [call.name for call in turn.tool_calls]
    results: list[CheckResult] = []
    if spec.none:
        results.append(_result("no_tool_calls", TOOL_SELECTION, not called, f"called: {called}"))
    if spec.expected is not None:
        passed, score = trajectory_match(called, spec.expected, spec.mode, turn)
        results.append(
            CheckResult(
                name=f"trajectory:{spec.mode}",
                category=TOOL_SELECTION,
                passed=passed,
                score=score,
                detail=f"expected {spec.expected}, called {called}",
            )
        )
    for name in spec.forbidden:
        hit = any(call.is_named(name) for call in turn.tool_calls)
        results.append(_result(f"forbidden:{name}", TOOL_SELECTION, not hit, "called" if hit else "not called"))
    if not spec.allow_failed:
        failed = [call.name for call in turn.tool_calls if call.status == "failed"]
        results.append(_result("no_failed_calls", TOOL_SELECTION, not failed, f"failed: {failed}"))
    return results


def trajectory_match(called: list[str], expected: list[str], mode: str, turn: TurnObservation) -> tuple[bool, float]:
    """Return (passed, F1 over the call multisets). Names may match the target id or the function name."""
    aliases = {call.function: call.name for call in turn.tool_calls if call.function}
    expected = [aliases.get(name, name) for name in expected]
    actual, wanted = Counter(called), Counter(expected)
    overlap = sum((actual & wanted).values())
    precision = overlap / sum(actual.values()) if actual else (1.0 if not wanted else 0.0)
    recall = overlap / sum(wanted.values()) if wanted else 1.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    if mode == "strict":
        passed = called == expected
    elif mode == "unordered":
        passed = actual == wanted
    elif mode == "superset":
        passed = not (wanted - actual)
    else:  # subset
        passed = not (actual - wanted)
    return passed, f1


def check_tool_args(spec: ArgExpectation, turn: TurnObservation) -> CheckResult:
    candidates = [call for call in turn.tool_calls if call.is_named(spec.tool)]
    name = f"args:{spec.tool}"
    if not candidates:
        return _result(name, TOOL_ARGUMENTS, False, f"{spec.tool} was not called")
    for call in candidates:
        arguments = call.arguments if isinstance(call.arguments, dict) else {}
        if spec.match == "exact":
            ok = _normalise(arguments, spec.case_insensitive) == _normalise(spec.args, spec.case_insensitive)
        else:
            ok = _subset_match(spec.args, arguments, case_insensitive=spec.case_insensitive)
        if ok:
            return _result(name, TOOL_ARGUMENTS, True, json.dumps(arguments, ensure_ascii=False, default=str))
    seen = [call.arguments for call in candidates]
    return _result(name, TOOL_ARGUMENTS, False, f"expected {spec.args} ({spec.match}); got {seen}")


# ---- output --------------------------------------------------------------------------------------------------


def check_output(spec: OutputExpectation, turn: TurnObservation) -> list[CheckResult]:
    text = turn.content
    fold = (lambda value: value.casefold()) if spec.case_insensitive else (lambda value: value)
    results: list[CheckResult] = []
    if spec.equals is not None:
        results.append(_result("output:equals", OUTPUT, fold(text) == fold(spec.equals.strip()), _clip(text)))
    for needle in spec.contains:
        results.append(_result(f"output:contains:{needle}", OUTPUT, fold(needle) in fold(text), _clip(text)))
    if spec.contains_any:
        hit = any(fold(needle) in fold(text) for needle in spec.contains_any)
        results.append(_result("output:contains_any", OUTPUT, hit, _clip(text)))
    for needle in spec.not_contains:
        results.append(_result(f"output:not_contains:{needle}", OUTPUT, fold(needle) not in fold(text), _clip(text)))
    if spec.regex is not None:
        flags = re.IGNORECASE | re.DOTALL if spec.case_insensitive else re.DOTALL
        results.append(_result("output:regex", OUTPUT, re.search(spec.regex, text, flags) is not None, _clip(text)))
    return results


# ---- context management --------------------------------------------------------------------------------------


def check_prompt(spec: PromptExpectation, turn: TurnObservation) -> list[CheckResult]:
    requests = turn.model_requests()
    if not requests:
        return [_result("prompt:available", CONTEXT, False, "no LLM request payloads visible for this trace")]
    first_request = requests[0]
    text = turn.prompt_text()
    results = [
        _result(f"prompt:contains:{needle}", CONTEXT, needle in text, "present" if needle in text else "missing")
        for needle in spec.contains
    ]
    results += [
        _result(f"prompt:not_contains:{needle}", CONTEXT, needle not in text, "leaked" if needle in text else "absent")
        for needle in spec.not_contains
    ]
    if spec.has_summary is not None:
        has = any(
            message.get("role") == "system" and str(message.get("content", "")).startswith(SUMMARY_PREFIX)
            for message in first_request
        )
        results.append(_result("prompt:has_summary", CONTEXT, has == spec.has_summary, f"summary present={has}"))
    if spec.max_messages is not None:
        count = len(first_request)
        results.append(_result("prompt:max_messages", CONTEXT, count <= spec.max_messages, f"{count} messages"))
    return results


# ---- performance ---------------------------------------------------------------------------------------------


def check_llm_calls(spec: LlmCallsExpectation, turn: TurnObservation) -> list[CheckResult]:
    results: list[CheckResult] = []
    if spec.served_model is not None:
        served = [str((call.get("response") or {}).get("model") or "") for call in turn.agent_llm_calls()]
        passed = bool(served) and all(model == spec.served_model for model in served)
        detail = f"served by {served}" if served else "no agent LLM calls recorded for this trace"
        results.append(_result(f"llm_calls:served_model:{spec.served_model}", CONTEXT, passed, detail))
    return results


def check_budget(spec: Budget, turn: TurnObservation) -> list[CheckResult]:
    results: list[CheckResult] = []

    def upper(name: str, actual: float | None, limit: float | None) -> None:
        if limit is None:
            return
        if actual is None:
            results.append(_result(name, PERFORMANCE, False, "not measured"))
        else:
            results.append(_result(name, PERFORMANCE, actual <= limit, f"{actual:.0f} <= {limit:.0f}"))

    upper("budget:iterations", turn.iterations, spec.max_iterations)
    upper("budget:latency_ms", turn.latency_ms, spec.max_latency_ms)
    upper("budget:ttft_ms", turn.ttft_ms, spec.max_ttft_ms)
    upper("budget:total_tokens", turn.input_tokens + turn.output_tokens, spec.max_total_tokens)
    upper("budget:tool_calls", len(turn.tool_calls), spec.max_tool_calls)
    if spec.max_duplicate_calls is not None:
        counts = Counter(call.signature() for call in turn.tool_calls)
        duplicates = sum(count - 1 for count in counts.values())
        upper("budget:duplicate_calls", duplicates, spec.max_duplicate_calls)
    return results


# ---- helpers -------------------------------------------------------------------------------------------------


def _result(name: str, category: str, passed: bool, detail: str = "") -> CheckResult:
    return CheckResult(name=name, category=category, passed=passed, score=1.0 if passed else 0.0, detail=detail)


def _subset_match(expected: Any, actual: Any, *, case_insensitive: bool) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(
            key in actual and _subset_match(value, actual[key], case_insensitive=case_insensitive)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        return all(
            any(_subset_match(item, candidate, case_insensitive=case_insensitive) for candidate in actual)
            for item in expected
        )
    if isinstance(expected, str) and expected.startswith("re:"):
        return re.search(expected[3:], str(actual), re.IGNORECASE if case_insensitive else 0) is not None
    if isinstance(expected, str) and isinstance(actual, str) and case_insensitive:
        return expected.strip().casefold() == actual.strip().casefold()
    return expected == actual


def _normalise(value: Any, case_insensitive: bool) -> Any:
    if isinstance(value, dict):
        return {key: _normalise(item, case_insensitive) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalise(item, case_insensitive) for item in value]
    if isinstance(value, str) and case_insensitive:
        return value.strip().casefold()
    return value


def _clip(text: str, limit: int = 300) -> str:
    return text if len(text) <= limit else text[:limit] + "..."
