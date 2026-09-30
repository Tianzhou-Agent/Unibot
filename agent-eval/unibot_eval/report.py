"""Aggregate metrics and write ``report.json`` + ``report.md``."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

from unibot_eval.html_report import write_html
from unibot_eval.runner import RunResult


def summarize(run: RunResult) -> dict[str, Any]:
    cases = run.cases
    attempts = [attempt for case in cases for attempt in case.attempts]
    turns = [turn for attempt in attempts for turn in attempt.turns if turn["action"] == "chat"]

    by_tag: dict[str, list] = defaultdict(list)
    for case in cases:
        for tag in case.case.tags or ["untagged"]:
            by_tag[tag].append(case)

    by_category: dict[str, list] = defaultdict(list)
    for attempt in attempts:
        for check in attempt.checks:
            by_category[check.category].append(check)

    return {
        "started_at": run.started_at,
        "finished_at": run.finished_at,
        "settings": run.settings,
        "overall": {
            "cases": len(cases),
            "attempts": len(attempts),
            "attempt_pass_rate": _rate([attempt.passed for attempt in attempts]),
            "pass_at_k_rate": _rate([case.pass_at_k for case in cases]),
            "pass_hat_k_rate": _rate([case.pass_hat_k for case in cases]),
            "mean_progress": round(statistics.fmean(case.progress for case in cases), 4) if cases else 0.0,
        },
        "by_tag": {
            tag: {
                "cases": len(items),
                "pass_hat_k_rate": _rate([case.pass_hat_k for case in items]),
                "attempt_pass_rate": _rate([attempt.passed for case in items for attempt in case.attempts]),
            }
            for tag, items in sorted(by_tag.items())
        },
        "by_category": {
            category: {
                "checks": len(items),
                "pass_rate": _rate([check.passed for check in items]),
                "mean_score": round(statistics.fmean(check.score for check in items), 4),
            }
            for category, items in sorted(by_category.items())
        },
        "performance": {
            "turns": len(turns),
            "latency_ms": _percentiles([turn["latency_ms"] for turn in turns]),
            "ttft_ms": _percentiles([turn["ttft_ms"] for turn in turns]),
            "input_tokens_mean": _mean([turn["input_tokens"] for turn in turns]),
            "output_tokens_mean": _mean([turn["output_tokens"] for turn in turns]),
            "iterations_mean": _mean([turn["iterations"] for turn in turns]),
            "tool_calls_mean": _mean([len(turn["tool_calls"]) for turn in turns]),
        },
    }


def write_reports(run: RunResult, out_dir: str | Path) -> dict[str, Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    summary = summarize(run)
    json_path = out / "report.json"
    data = json.dumps({"summary": summary, "cases": [case.to_dict() for case in run.cases]}, ensure_ascii=False,
                      indent=2, default=str)
    json_path.write_text(data, encoding="utf-8")
    md_path = out / "report.md"
    md_path.write_text(render_markdown(run, summary), encoding="utf-8")
    html_path = write_html([(out.name, json.loads(data))], out / "report.html")
    return {"json": json_path, "markdown": md_path, "html": html_path}


def render_markdown(run: RunResult, summary: dict[str, Any] | None = None) -> str:
    summary = summary or summarize(run)
    overall = summary["overall"]
    perf = summary["performance"]
    lines = [
        "# Unibot agent evaluation",
        "",
        f"Backend `{summary['settings']['base_url']}` · judge `{summary['settings']['judge_model'] or 'disabled'}` · "
        f"{summary['started_at']} → {summary['finished_at']}",
        "",
        "| Cases | Attempts | pass^k (all attempts pass) | pass@k (any attempt passes) | Attempt pass rate | "
        "Mean progress (checks passed) |",
        "|---:|---:|---:|---:|---:|---:|",
        f"| {overall['cases']} | {overall['attempts']} | {_pct(overall['pass_hat_k_rate'])} | "
        f"{_pct(overall['pass_at_k_rate'])} | {_pct(overall['attempt_pass_rate'])} | "
        f"{_pct(overall['mean_progress'])} |",
        "",
        "## Behaviour by check category",
        "",
        "| Category | Checks | Pass rate | Mean score |",
        "|---|---:|---:|---:|",
    ]
    lines += [
        f"| {name} | {row['checks']} | {_pct(row['pass_rate'])} | {row['mean_score']:.2f} |"
        for name, row in summary["by_category"].items()
    ]
    lines += ["", "## By tag", "", "| Tag | Cases | pass^k | Attempt pass rate |", "|---|---:|---:|---:|"]
    lines += [
        f"| {tag} | {row['cases']} | {_pct(row['pass_hat_k_rate'])} | {_pct(row['attempt_pass_rate'])} |"
        for tag, row in summary["by_tag"].items()
    ]
    lines += [
        "",
        "## Performance (per chat turn)",
        "",
        "| Metric | p50 | p95 | max |",
        "|---|---:|---:|---:|",
        _perf_row("Latency (ms)", perf["latency_ms"]),
        _perf_row("Time to first token (ms)", perf["ttft_ms"]),
        "",
        f"Mean input tokens {perf['input_tokens_mean']} · mean output tokens {perf['output_tokens_mean']} · "
        f"mean iterations {perf['iterations_mean']} · mean capability calls {perf['tool_calls_mean']}",
        "",
        "## Cases",
        "",
        "| Case | Tags | k | Pass rate | Progress | pass^k |",
        "|---|---|---:|---:|---:|:---:|",
    ]
    lines += [
        f"| `{case.case.id}` | {', '.join(case.case.tags)} | {case.k} | {_pct(case.pass_rate)} | "
        f"{_pct(case.progress)} | {'✅' if case.pass_hat_k else '❌'} |"
        for case in run.cases
    ]
    failures = [(case, attempt) for case in run.cases for attempt in case.attempts if not attempt.passed]
    if failures:
        lines += ["", "## Failures", ""]
        for case, attempt in failures:
            lines.append(f"### `{case.case.id}` attempt {attempt.attempt}")
            if attempt.error:
                lines.append(f"- **error**: {attempt.error}")
            for check in attempt.checks:
                if not check.passed:
                    detail = check.detail.replace("\n", " ")[:400]
                    where = "whole attempt" if check.turn is None else f"turn {check.turn}"
                    lines.append(f"- {where} · `{check.name}` ({check.category}): {detail}")
            traces = [turn["trace_id"] for turn in attempt.turns if turn.get("trace_id")]
            if traces:
                lines.append(f"- backend traces: {', '.join(f'`{trace}`' for trace in traces)}")
            lines.append("")
    notes = sorted({note for case in run.cases for attempt in case.attempts for note in attempt.notes})
    if notes:
        lines += ["", "## Notes", ""] + [f"- {note}" for note in notes]
    return "\n".join(lines) + "\n"


def _rate(values: list[bool]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def _mean(values: list[Any]) -> float | None:
    numbers = [value for value in values if isinstance(value, (int, float))]
    return round(statistics.fmean(numbers), 1) if numbers else None


def _percentiles(values: list[Any]) -> dict[str, float | None]:
    numbers = sorted(value for value in values if isinstance(value, (int, float)))
    if not numbers:
        return {"p50": None, "p95": None, "max": None}

    def pick(q: float) -> float:
        return round(numbers[min(len(numbers) - 1, int(round(q * (len(numbers) - 1))))], 1)

    return {"p50": pick(0.5), "p95": pick(0.95), "max": round(numbers[-1], 1)}


def _perf_row(label: str, values: dict[str, float | None]) -> str:
    def fmt(value: float | None) -> str:
        return "–" if value is None else f"{value:,.0f}"

    return f"| {label} | {fmt(values['p50'])} | {fmt(values['p95'])} | {fmt(values['max'])} |"


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"
