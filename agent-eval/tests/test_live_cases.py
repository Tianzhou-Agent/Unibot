"""Every dataset case as a pytest test against a running backend (CI-friendly, JUnit via --junitxml).

    UNIBOT_EVAL_BASE_URL=http://127.0.0.1:8000 UNIBOT_EVAL_REPEATS=3 pytest -m live -k context

A case passes only when all k attempts pass (pass^k). Skipped unless UNIBOT_EVAL_BASE_URL is set.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from unibot_eval.config import EvalSettings
from unibot_eval.dataset import load_cases, select_cases
from unibot_eval.runner import EvalRunner

DATASETS = Path(__file__).resolve().parent.parent / "datasets"
ENABLED = [item for item in os.getenv("UNIBOT_EVAL_ENABLE", "").split(",") if item]
CASES = select_cases(load_cases([DATASETS]), enable=ENABLED)

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.getenv("UNIBOT_EVAL_BASE_URL"), reason="set UNIBOT_EVAL_BASE_URL to run live evals"),
]


@pytest.fixture(scope="session")
def runner() -> EvalRunner:
    return EvalRunner(EvalSettings.from_env(), use_judge=os.getenv("UNIBOT_EVAL_JUDGE", "true") != "false")


@pytest.mark.parametrize("case", CASES, ids=[case.id for case in CASES])
def test_case(case, runner: EvalRunner) -> None:
    result = asyncio.run(runner.run_case(case))
    problems = [
        f"attempt {attempt.attempt}: " + (attempt.error or "; ".join(
            f"turn {check.turn} {check.name}: {check.detail[:200]}" for check in attempt.checks if not check.passed
        ))
        for attempt in result.attempts
        if not attempt.passed
    ]
    assert result.pass_hat_k, f"{case.id} passed {result.pass_rate:.0%} of {result.k} attempts\n" + "\n".join(problems)
