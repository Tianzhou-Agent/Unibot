"""Command line entry point.

    python -m unibot_eval run --dataset datasets --k 3 --out reports/latest
    python -m unibot_eval run --tags tool_use,context --no-judge
    python -m unibot_eval list
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from unibot_eval.config import EvalSettings
from unibot_eval.dataset import load_cases, select_cases
from unibot_eval.report import summarize, write_reports
from unibot_eval.runner import EvalRunner
from unibot_eval.telemetry import setup_tracing, shutdown_tracing

DEFAULT_DATASET = Path(__file__).resolve().parent.parent / "datasets"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="unibot-eval", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(command: argparse.ArgumentParser) -> None:
        command.add_argument("--dataset", action="append", help="YAML file or directory (repeatable)")
        command.add_argument("--tags", help="comma-separated tags to include")
        command.add_argument("--case", action="append", help="case id or glob, e.g. 'context.*' (repeatable)")
        command.add_argument("--enable", default="",
                             help="comma-separated optional requirements to enable, e.g. 'compression'")

    run = sub.add_parser("run", help="run evaluations against a backend")
    common(run)
    run.add_argument("--base-url", help="backend URL (default: UNIBOT_EVAL_BASE_URL or http://127.0.0.1:8000)")
    run.add_argument("--k", type=int, help="attempts per case (overrides dataset/UNIBOT_EVAL_REPEATS)")
    run.add_argument("--concurrency", type=int, help="parallel attempts")
    run.add_argument("--no-stream", action="store_true", help="use POST /chat instead of /chat/stream (no TTFT)")
    run.add_argument("--no-judge", action="store_true", help="deterministic checks only")
    run.add_argument("--out", default="reports/latest", help="report directory")
    run.add_argument("--min-pass-rate", type=float, default=1.0,
                     help="exit non-zero when the pass^k case rate is below this (default 1.0)")

    sub.add_parser("list", help="list selected cases")
    common(sub.choices["list"])

    args = parser.parse_args(argv)
    cases = select_cases(
        load_cases(args.dataset or [DEFAULT_DATASET]),
        tags=_split(args.tags),
        ids=args.case,
        enable=_split(args.enable),
    )
    if args.command == "list":
        for case in cases:
            print(f"{case.id:40} {','.join(case.tags):30} {len(case.turns)} turn(s)  {case.description}")
        return 0
    if not cases:
        print("no cases selected", file=sys.stderr)
        return 2

    settings = EvalSettings.from_env()
    if args.base_url:
        settings.base_url = args.base_url
    if args.concurrency:
        settings.concurrency = args.concurrency
    if args.no_stream:
        settings.stream = False

    setup_tracing()
    try:
        result = asyncio.run(EvalRunner(settings, use_judge=not args.no_judge).run(cases, repeats=args.k))
    finally:
        shutdown_tracing()
    paths = write_reports(result, args.out)
    overall = summarize(result)["overall"]
    print(
        f"{overall['cases']} cases / {overall['attempts']} attempts | "
        f"pass^k {overall['pass_hat_k_rate']:.0%} | pass@k {overall['pass_at_k_rate']:.0%} | "
        f"attempts {overall['attempt_pass_rate']:.0%}"
    )
    for case in result.cases:
        mark = "PASS" if case.pass_hat_k else "FAIL"
        print(f"  [{mark}] {case.case.id} ({sum(a.passed for a in case.attempts)}/{case.k})")
    print(f"reports: {paths['markdown']} , {paths['json']}")
    return 0 if overall["pass_hat_k_rate"] >= args.min_pass_rate else 1


def _split(value: str | None) -> list[str] | None:
    items = [item.strip() for item in (value or "").split(",") if item.strip()]
    return items or None


if __name__ == "__main__":
    raise SystemExit(main())
