"""Command line entry point.

    python -m unibot_eval run --dataset datasets --k 3 --out reports/latest
    python -m unibot_eval run --tags tool_use,context --no-judge
    python -m unibot_eval run --case "long.*" --show-in-ui     # watch the runs in the Unibot UI
    python -m unibot_eval report reports/a/report.json reports/b/report.json --out reports/all.html
    python -m unibot_eval list
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from unibot_eval.client import UnibotEvalClient
from unibot_eval.config import EvalSettings
from unibot_eval.dataset import Case, load_cases, select_cases
from unibot_eval.html_report import load_run, write_html
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
    run.add_argument("--show-in-ui", action="store_true",
                     help="run as the user the Unibot UI shows (dev auth bypass, session cookie or no auth), keep "
                          "the conversations and title them '[eval] <case id> #<attempt>'; one attempt per case "
                          "unless --k is given")

    sub.add_parser("list", help="list selected cases")
    common(sub.choices["list"])

    report = sub.add_parser("report", help="merge report.json files of several runs into one HTML page")
    report.add_argument("runs", nargs="+", help="report.json files; each run is labelled by its directory name")
    report.add_argument("--out", default="reports/report.html", help="HTML file to write")

    args = parser.parse_args(argv)
    if args.command == "report":
        path = write_html([(Path(run).parent.name, load_run(run)) for run in args.runs], args.out)
        print(f"report: {path}")
        return 0
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

    repeats = args.k
    try:
        user = asyncio.run(_request_user(settings))
    except httpx.HTTPError as exc:
        if args.show_in_ui:
            print(f"cannot reach {settings.base_url}: {exc}", file=sys.stderr)
            return 2
        user = None  # the attempts report the connection errors themselves
    if args.show_in_ui:
        if user is None:
            print("--show-in-ui needs a backend whose UI user these requests act as: start it with "
                  "UNIBOT_ENV=development and UNIBOT_DEV_AUTH_BYPASS=true, or put a session cookie in "
                  "UNIBOT_EVAL_HEADERS", file=sys.stderr)
            return 2
        settings.ui_user_id, settings.tenant_id = user
        settings.keep_conversations = True
        repeats = args.k or 1
        cases, skipped = split_single_user(cases)
        for case in skipped:
            print(f"  [SKIP] {case.id}: needs separate users, which one UI user cannot provide")
        if not cases:
            print("no single-user cases selected", file=sys.stderr)
            return 2
        print(f"running as UI user {settings.ui_user_id}: conversations are kept and titled '[eval] <case id> "
              f"#<attempt>'. Memory and document changes stay in this user's account.")
    elif user is not None and user[0] != "anonymous":
        print(f"warning: the backend runs every request as user {user[0]} (dev auth bypass or session cookie), so "
              "the per-attempt eval users are ignored: attempts share memories and documents, and cross-user "
              "isolation cases cannot pass.", file=sys.stderr)

    setup_tracing()
    try:
        result = asyncio.run(EvalRunner(settings, use_judge=not args.no_judge).run(cases, repeats=repeats))
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
    if args.show_in_ui:
        for case in result.cases:
            for attempt in case.attempts:
                ids = list(dict.fromkeys(t["conversation_id"] for t in attempt.turns if t.get("conversation_id")))
                print(f"  {case.case.id} #{attempt.attempt}: {', '.join(ids) or 'no conversation'}")
    print(f"reports: {paths['html']} , {paths['markdown']} , {paths['json']}")
    return 0 if overall["pass_hat_k_rate"] >= args.min_pass_rate else 1


def split_single_user(cases: list[Case]) -> tuple[list[Case], list[Case]]:
    """(cases one user can run, cases that need several users). --show-in-ui has only the UI user."""
    kept = [case for case in cases if len({turn.actor for turn in case.turns}) == 1]
    return kept, [case for case in cases if case not in kept]


async def _request_user(settings: EvalSettings) -> tuple[str, str] | None:
    async with UnibotEvalClient(settings.base_url, timeout_s=30, headers=settings.headers) as client:
        return await client.request_user()


def _split(value: str | None) -> list[str] | None:
    items = [item.strip() for item in (value or "").split(",") if item.strip()]
    return items or None


if __name__ == "__main__":
    raise SystemExit(main())
