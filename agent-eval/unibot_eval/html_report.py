"""Self-contained HTML view of one or more runs (``report.json``): summary, categories, and every case with its
failed checks and transcript. Written next to ``report.md`` and by ``python -m unibot_eval report``."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from typing import Any

_CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;--ok:#1f7a4d;--ok-bg:#e3f4ea;
--bad:#b3261e;--bad-bg:#fbe6e4;--warn:#8a5a00;--warn-bg:#fdf1d8;--bar:#d9d9d3;--accent:#3056d3}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--ink:#ecece8;--muted:#a3a39c;--line:#34342f;
--ok:#6fcf97;--ok-bg:#16301f;--bad:#f28b82;--bad-bg:#3a1c1a;--warn:#f2c46b;--warn-bg:#3a2e14;--bar:#3a3a35;
--accent:#8aa4ff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);
font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:22px;margin:0 0 4px}
h2{font-size:17px;margin:32px 0 10px}h3{font-size:15px;margin:22px 0 8px;color:var(--muted)}
.meta{color:var(--muted);font-size:12.5px;overflow-wrap:anywhere}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:18px 0}
.tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.tile b{display:block;font-size:24px;font-variant-numeric:tabular-nums}.tile span{color:var(--muted);font-size:12px}
.scroll{overflow-x:auto}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:10px;
overflow:hidden}th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:12px;color:var(--muted);font-weight:600}td.num,th.num{text-align:right;font-variant-numeric:tabular-nums}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600}
.pass{background:var(--ok-bg);color:var(--ok)}.fail{background:var(--bad-bg);color:var(--bad)}
.flaky{background:var(--warn-bg);color:var(--warn)}
.bar{height:6px;background:var(--bar);border-radius:3px;min-width:80px}.bar i{display:block;height:100%;
border-radius:3px;background:var(--ok)}.bar.low i{background:var(--bad)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:8px 0}
summary{cursor:pointer;padding:10px 14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
summary code{font-weight:600;overflow-wrap:anywhere}.body{padding:4px 14px 14px;border-top:1px solid var(--line)}
.check{margin:4px 0;font-size:13px}.check code{background:var(--bad-bg);color:var(--bad);padding:0 4px;border-radius:4px}
.turn{border-left:3px solid var(--line);margin:10px 0;padding:2px 0 2px 12px}
.turn .who{font-size:12px;color:var(--muted)}.turn pre{white-space:pre-wrap;word-break:break-word;margin:4px 0;
font:12.5px/1.45 ui-monospace,Consolas,monospace}.tools{font-size:12px;color:var(--muted)}
.tools .f{color:var(--bad)}.ids{font-size:11.5px;color:var(--muted);word-break:break-all}
.tags{color:var(--muted);font-size:12px}.note{font-size:12.5px;color:var(--warn)}
@media (max-width:640px){.hide-sm{display:none}}
"""


def render_html(runs: list[tuple[str, dict[str, Any]]]) -> str:
    """``runs``: (label, parsed report.json). One page for all of them."""
    cases = [(label, case) for label, run in runs for case in run["cases"]]
    attempts = [attempt for _, case in cases for attempt in case["attempts"]]
    passed = sum(case["pass_hat_k"] for _, case in cases)
    flaky = sum(case["pass_at_k"] and not case["pass_hat_k"] for _, case in cases)
    progress = sum(case.get("progress", 0) for _, case in cases) / len(cases) if cases else 0.0
    parts = [
        "<!doctype html><html lang=en><head><meta charset=utf-8>",
        "<meta name=viewport content='width=device-width,initial-scale=1'><title>Unibot eval results</title>",
        f"<style>{_CSS}</style></head><body><main>",
        "<h1>Unibot agent eval results</h1>",
        "<div class=meta>" + " · ".join(
            f"{escape(label)}: {escape(str(run['summary']['settings'].get('base_url')))}, judge "
            f"{escape(str(run['summary']['settings'].get('judge_model') or 'disabled'))}, "
            f"{escape(run['summary']['started_at'][:19])}"
            for label, run in runs
        ) + "</div>",
        "<div class=tiles>",
        _tile(f"{passed}/{len(cases)}", "cases passing every attempt (pass^k)"),
        _tile(str(flaky), "flaky cases (pass@k but not pass^k)"),
        _tile(f"{sum(a['passed'] for a in attempts)}/{len(attempts)}", "attempts passed"),
        _tile(_pct(progress), "mean progress (checks passed)"),
        "</div>",
        "<h2>Checks by category</h2>",
        _categories(attempts),
        "<h2>Cases</h2>",
    ]
    by_source: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for label, case in cases:
        by_source.setdefault(Path(case.get("source") or "unknown").name, []).append((label, case))
    for source, items in by_source.items():
        ok = sum(case["pass_hat_k"] for _, case in items)
        parts.append(f"<h3>{escape(source)} · {ok}/{len(items)} passing</h3>")
        parts += [_case(label, case, multiple=len(runs) > 1) for label, case in items]
    parts.append("</main></body></html>")
    return "".join(parts)


def write_html(runs: list[tuple[str, dict[str, Any]]], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_html(runs), encoding="utf-8")
    return target


def load_run(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _tile(value: str, label: str) -> str:
    return f"<div class=tile><b>{escape(value)}</b><span>{escape(label)}</span></div>"


def _categories(attempts: list[dict[str, Any]]) -> str:
    rows: dict[str, list[bool]] = {}
    for attempt in attempts:
        for check in attempt["checks"]:
            rows.setdefault(check["category"], []).append(check["passed"])
    body = "".join(
        f"<tr><td>{escape(name)}</td><td class=num>{len(values)}</td><td class=num>{_pct(sum(values) / len(values))}"
        f"</td><td class=hide-sm>{_bar(sum(values) / len(values))}</td></tr>"
        for name, values in sorted(rows.items())
    )
    return ("<div class=scroll><table><tr><th>Category</th><th class=num>Checks</th><th class=num>Pass rate</th>"
            f"<th class=hide-sm></th></tr>{body}</table></div>")


def _case(label: str, case: dict[str, Any], *, multiple: bool) -> str:
    if case["pass_hat_k"]:
        status = "<span class='pill pass'>PASS</span>"
    elif case["pass_at_k"]:
        status = "<span class='pill flaky'>FLAKY</span>"
    else:
        status = "<span class='pill fail'>FAIL</span>"
    wins = sum(attempt["passed"] for attempt in case["attempts"])
    run = f" <span class=tags>[{escape(label)}]</span>" if multiple else ""
    head = (f"<summary>{status}<code>{escape(case['id'])}</code>{run}<span class=tags>{wins}/{case['k']} attempts · "
            f"progress {_pct(case.get('progress', 0))} · {escape(', '.join(case['tags']))}</span></summary>")
    body = [f"<div class=body><p class=meta>{escape(case.get('description') or '')}</p>"]
    for attempt in case["attempts"]:
        body.append(_attempt(attempt, show_number=case["k"] > 1))
    body.append("</div>")
    return f"<details{' open' if not case['pass_hat_k'] else ''}>{head}{''.join(body)}</details>"


def _attempt(attempt: dict[str, Any], *, show_number: bool) -> str:
    parts = []
    if show_number:
        mark = "passed" if attempt["passed"] else "failed"
        parts.append(f"<h3>Attempt {attempt['attempt']} · {mark} · {attempt['duration_ms'] / 1000:.0f}s</h3>")
    if attempt.get("error"):
        parts.append(f"<div class=check><code>error</code> {escape(attempt['error'][:600])}</div>")
    for check in attempt["checks"]:
        if not check["passed"]:
            where = "whole attempt" if check.get("turn") is None else f"turn {check['turn']}"
            parts.append(f"<div class=check>{where} · <code>{escape(check['name'])}</code> "
                         f"<span class=tags>({escape(check['category'])})</span> {escape(check['detail'][:500])}</div>")
    for note in attempt.get("notes") or []:
        parts.append(f"<div class=note>{escape(note)}</div>")
    for turn in attempt["turns"]:
        parts.append(_turn(turn))
    return "".join(parts)


def _turn(turn: dict[str, Any]) -> str:
    action = turn["action"]
    if action in {"http", "poll"}:
        return f"<div class=turn><div class=who>step {turn['index']} · {escape(str(turn.get('input')))}</div></div>"
    who = f"turn {turn['index']} · {escape(action)} · {escape(str(turn.get('status')))}"
    if turn.get("latency_ms"):
        who += f" · {turn['latency_ms'] / 1000:.1f}s · {turn.get('input_tokens', 0) + turn.get('output_tokens', 0)} tok"
    calls = " → ".join(
        f"<span class={'f' if call['status'] == 'failed' else ''}>{escape(call['name'])}</span>"
        for call in turn.get("tool_calls") or []
    )
    ids = " · ".join(f"{key} {escape(str(turn[key]))}" for key in ("conversation_id", "trace_id") if turn.get(key))
    return (
        f"<div class=turn><div class=who>{who}</div>"
        + (f"<pre>▶ {escape(turn['input'])}</pre>" if turn.get("input") else "")
        + (f"<div class=tools>tools: {calls}</div>" if calls else "")
        + (f"<pre>◀ {escape(turn['content'])}</pre>" if turn.get("content") else "")
        + (f"<div class=ids>{ids}</div>" if ids else "")
        + "</div>"
    )


def _bar(value: float) -> str:
    return f"<div class='bar{' low' if value < 0.8 else ''}'><i style='width:{value * 100:.0f}%'></i></div>"


def _pct(value: float) -> str:
    return f"{value * 100:.0f}%"
