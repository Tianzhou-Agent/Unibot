"""Offline tests of the harness itself (fake backend, fake judge; no network, no model quota)."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableLambda
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from tests.fake_backend import FakeUnibot
from unibot_eval import telemetry
from unibot_eval.checks import check_tool_args, trajectory_match
from unibot_eval.config import EvalSettings
from unibot_eval.dataset import ArgExpectation, Case, fresh_vars, load_cases, select_cases
from unibot_eval.judge import LLMJudge, Verdict
from unibot_eval.report import summarize, write_reports
from unibot_eval.runner import EvalRunner
from unibot_eval.trajectory import TurnObservation, extract_tool_calls

DATASETS = Path(__file__).resolve().parent.parent / "datasets"


# ---- trace normalisation -------------------------------------------------------------------------------------

REAL_SHAPED_TRACE = {
    # Mirrors backend/tests/test_chat_api.py::test_tool_loop_executes_remote_tool_and_records_trace
    "trace_id": "t1",
    "status": "completed",
    "events": [
        {"kind": "user.request", "status": "completed", "details": {"message_id": "msg_1"}},
        {"kind": "capability.discovery", "status": "completed", "details": {}},
        {"kind": "tool.requested", "status": "started", "target_id": "demo.add",
         "details": {"call_id": "call_1", "arguments": {"a": 17, "b": 25, "api_key": "[REDACTED]"}}},
        {"kind": "tool.completed", "status": "completed", "target_id": "demo.add", "duration_ms": 8.0,
         "details": {"call_id": "call_1", "result": {"result": 42}}},
        {"kind": "builtin.completed", "status": "completed", "target_id": "list_app", "details": {}},
        {"kind": "final.response", "status": "completed", "details": {"content": "The result is 42."}},
    ],
    "spans": [
        {"span_id": "root", "kind": "agent", "name": "agent.run", "status": "completed"},
        {"span_id": "s1", "parent_span_id": "root", "kind": "tool", "name": "demo.add", "status": "completed",
         "target_id": "demo.add", "logical_call_id": "call_1", "input": {"a": 17, "b": 25}, "output": {"result": 42}},
        {"span_id": "m1", "kind": "model", "name": "model", "status": "completed",
         "input": {"messages": [{"role": "user", "content": "What is 17 + 25?"}]}},
    ],
}


def test_extracts_calls_from_events_and_spans() -> None:
    calls = extract_tool_calls(REAL_SHAPED_TRACE)
    assert [call.name for call in calls] == ["demo.add", "list_app"]
    add = calls[0]
    assert add.arguments == {"a": 17, "b": 25, "api_key": "[REDACTED]"}
    assert add.result == {"result": 42} and add.status == "completed" and add.duration_ms == 8.0
    assert calls[1].kind == "builtin" and calls[1].status == "completed"


def test_pending_call_is_marked_when_run_waits_for_approval() -> None:
    trace = {"status": "approval_required", "events": [
        {"kind": "builtin.requested", "target_id": "memory.forget", "details": {"call_id": "c"}}]}
    assert extract_tool_calls(trace)[0].status == "pending_approval"


def test_spans_are_used_when_no_capability_events_exist() -> None:
    trace = {"events": [], "spans": [{"kind": "aina", "name": "x", "target_id": "unibot-memory",
                                      "input": {"q": 1}, "status": "completed"}]}
    call = extract_tool_calls(trace)[0]
    assert call.name == "unibot-memory" and call.arguments == {"q": 1} and call.status == "completed"


# ---- scorers -------------------------------------------------------------------------------------------------


def _turn(trace: dict) -> TurnObservation:
    turn = TurnObservation(index=0, actor="a", user_id="u", action="chat", input="x", response={}, trace=trace)
    turn.tool_calls = extract_tool_calls(trace)
    return turn


@pytest.mark.parametrize(
    ("called", "expected", "mode", "passed"),
    [
        (["a", "b"], ["a", "b"], "strict", True),
        (["b", "a"], ["a", "b"], "strict", False),
        (["b", "a"], ["a", "b"], "unordered", True),
        (["a", "b", "c"], ["a", "b"], "superset", True),
        (["a"], ["a", "b"], "superset", False),
        (["a"], ["a", "b"], "subset", True),
        (["a", "c"], ["a", "b"], "subset", False),
        ([], [], "strict", True),
    ],
)
def test_trajectory_modes(called: list[str], expected: list[str], mode: str, passed: bool) -> None:
    assert trajectory_match(called, expected, mode, _turn({}))[0] is passed


def test_trajectory_f1_rewards_partial_overlap() -> None:
    _, score = trajectory_match(["a", "x"], ["a", "b"], "strict", _turn({}))
    assert score == pytest.approx(0.5)


def test_argument_matching() -> None:
    turn = _turn(REAL_SHAPED_TRACE)
    assert check_tool_args(ArgExpectation(tool="demo.add", args={"a": 17}), turn).passed
    assert check_tool_args(ArgExpectation(tool="demo.add", args={"api_key": "re:REDACT"}), turn).passed
    assert not check_tool_args(ArgExpectation(tool="demo.add", args={"a": 18}), turn).passed
    assert not check_tool_args(ArgExpectation(tool="demo.add", args={"a": 17}, match="exact"), turn).passed
    assert not check_tool_args(ArgExpectation(tool="open_aina", args={}), turn).passed


# ---- datasets ------------------------------------------------------------------------------------------------


def test_shipped_datasets_are_valid_and_render() -> None:
    cases = load_cases([DATASETS])
    assert len(cases) >= 15
    rendered = cases[0].render(fresh_vars(cases[0]))
    assert "{{" not in rendered.model_dump_json()
    assert "context.compression_preserves_facts" not in {c.id for c in select_cases(cases)}
    assert "context.compression_preserves_facts" in {c.id for c in select_cases(cases, enable=["compression"])}


def test_deny_turns_do_not_expect_completed_status() -> None:
    case = Case.model_validate({"id": "x", "turns": [{"user": "hi"}, {"action": "deny_approval"}]})
    assert case.turns[0].expect.status == "completed"
    assert case.turns[1].expect.status is None
    assert case.render({}).turns[1].expect.status is None


# ---- runner end to end ---------------------------------------------------------------------------------------

E2E_CASES = [
    "chat.no_tool_exact_reply",
    "builtin.list_app",
    "builtin.open_aina",
    "context.recall_previous_turn",
    "context.new_conversation_is_clean",
    "context.cross_user_isolation",
    "safety.memory_lifecycle_with_approval",
]


def _run(fake: FakeUnibot, ids: list[str], *, stream: bool = True, judge: LLMJudge | None = None, k: int = 2):
    settings = EvalSettings(base_url="http://unibot.test", stream=stream, concurrency=4, trace_wait_s=0)
    cases = select_cases(load_cases([DATASETS]), ids=ids)
    runner = EvalRunner(settings, judge=judge, use_judge=judge is not None, transport=fake.transport())
    return asyncio.run(runner.run(cases, repeats=k))


@pytest.mark.parametrize("stream", [True, False])
def test_well_behaved_agent_passes(stream: bool) -> None:
    fake = FakeUnibot()
    result = _run(fake, E2E_CASES, stream=stream)
    failures = {
        case.case.id: [c.to_dict() for a in case.attempts for c in a.checks if not c.passed] + [a.error for a in
                                                                                               case.attempts if a.error]
        for case in result.cases
        if not case.pass_hat_k
    }
    assert failures == {}
    assert len(result.cases) == len(E2E_CASES) and all(case.k == 2 for case in result.cases)
    turn = result.cases[0].attempts[0].turns[0]
    assert turn["latency_ms"] is not None and (turn["ttft_ms"] is not None) is stream
    assert fake.deleted, "conversations are cleaned up"


def test_context_leak_is_caught_in_prompt_and_answer() -> None:
    result = _run(FakeUnibot(faults={"leak_context"}), ["context.cross_user_isolation"], k=1)
    failed = {c.name for c in result.cases[0].attempts[0].checks if not c.passed}
    assert any(name.startswith("prompt:not_contains:SECRET-") for name in failed)
    assert any(name.startswith("output:not_contains:SECRET-") for name in failed)


def test_wrong_tool_and_failed_tool_are_caught() -> None:
    wrong = _run(FakeUnibot(faults={"wrong_tool"}), ["builtin.list_app"], k=1).cases[0]
    assert "trajectory:strict" in {c.name for c in wrong.attempts[0].checks if not c.passed}
    failing = _run(FakeUnibot(faults={"fail_tool"}), ["builtin.list_app"], k=1).cases[0]
    assert "no_failed_calls" in {c.name for c in failing.attempts[0].checks if not c.passed}


def test_langchain_judge_receives_trajectory_and_scores() -> None:
    prompts: list[str] = []

    def fake_model(prompt_value) -> Verdict:  # stands in for chat_model.with_structured_output(Verdict)
        text = prompt_value.to_string()
        prompts.append(text)
        return Verdict(reasoning="grounded in list_app result", score=0.9)

    result = _run(FakeUnibot(), ["builtin.list_app"], judge=LLMJudge(RunnableLambda(fake_model)), k=1)
    judge_checks = [c for c in result.cases[0].attempts[0].checks if c.category.startswith("judge:")]
    assert [c.name for c in judge_checks] == ["judge:groundedness"] and judge_checks[0].passed
    assert "builtin:list_app" in prompts[0] and "unibot-memory" in prompts[0]


def test_judge_errors_fail_the_check_without_crashing() -> None:
    def broken(_):
        raise RuntimeError("judge offline")

    result = _run(FakeUnibot(), ["builtin.list_app"], judge=LLMJudge(RunnableLambda(broken)), k=1)
    check = next(c for c in result.cases[0].attempts[0].checks if c.category.startswith("judge:"))
    assert not check.passed and "judge offline" in check.detail


def test_capability_error_events_do_not_abort_the_stream() -> None:
    result = _run(FakeUnibot(faults={"capability_error"}), ["chat.no_tool_exact_reply"], k=1)
    attempt = result.cases[0].attempts[0]
    assert attempt.error is None and attempt.passed


def test_judge_falls_back_to_verdict_json_in_message_text() -> None:
    from langchain_core.messages import AIMessage

    from unibot_eval.judge import _verdict_or_content_json

    text = AIMessage(content='```json\n{"reasoning": "treated the note as data", "score": 1.0}\n```')
    assert _verdict_or_content_json({"parsed": None, "raw": text}) == Verdict(
        reasoning="treated the note as data", score=1.0
    )
    with pytest.raises(ValueError):
        _verdict_or_content_json({"parsed": None, "raw": AIMessage(content="<verdict><score>1</score></verdict>")})

    result = _run(FakeUnibot(), ["builtin.list_app"], judge=LLMJudge(RunnableLambda(lambda _: None)), k=1)
    check = next(c for c in result.cases[0].attempts[0].checks if c.category.startswith("judge:"))
    assert not check.passed and "no structured verdict" in check.detail


def test_otel_spans_and_trace_propagation() -> None:
    exporter = InMemorySpanExporter()
    telemetry.setup_tracing(exporter)
    try:
        fake = FakeUnibot()
        _run(fake, ["builtin.list_app"], k=1)
    finally:
        telemetry.shutdown_tracing()
    spans = {span.name: span for span in exporter.get_finished_spans()}
    assert {"eval.run", "eval.case", "eval.attempt", "eval.turn"} <= set(spans)
    turn = spans["eval.turn"]
    assert turn.attributes["unibot.trace_id"] in fake.traces
    assert list(turn.attributes["eval.tool_calls"]) == ["list_app"]
    assert any(event.name == "eval.check" for event in spans["eval.attempt"].events)
    assert fake.seen_traceparents and fake.seen_traceparents[0].split("-")[1] == format(
        turn.context.trace_id, "032x")


def test_reports_are_written(tmp_path: Path) -> None:
    result = _run(FakeUnibot(faults={"wrong_tool"}), ["builtin.list_app", "chat.no_tool_exact_reply"], k=2)
    paths = write_reports(result, tmp_path)
    summary = summarize(result)
    assert summary["overall"]["pass_hat_k_rate"] == 0.5
    assert summary["by_category"]["tool_selection"]["pass_rate"] < 1
    markdown = paths["markdown"].read_text(encoding="utf-8")
    assert "## Failures" in markdown and "builtin.list_app" in markdown
    page = paths["html"].read_text(encoding="utf-8")
    assert "1/2" in page and "builtin.list_app" in page and "trajectory:strict" in page and "List the apps." in page

    from unibot_eval.cli import main

    combined = tmp_path / "all.html"
    assert main(["report", str(paths["json"]), str(paths["json"]), "--out", str(combined)]) == 0
    assert combined.read_text(encoding="utf-8").count("<code>builtin.list_app</code>") == 2


def _step_case(served_model: str) -> Case:
    return Case.model_validate(
        {
            "id": "steps.setup_poll_and_served_model",
            "turns": [
                {
                    "action": "http",
                    "request": {
                        "path": "/model-settings/providers",
                        "body": {"user_id": "{{user_id}}", "name": "p"},
                        "save": {"provider_id": "id", "model_id": "models.0.id"},
                    },
                },
                {
                    "action": "poll",
                    "poll": {
                        "path": "/jobs/{{provider_id}}",
                        "params": {"user_id": "{{user_id}}"},
                        "field": "items.0.status",
                        "until": ["done", "failed"],
                        "interval_s": 0,
                        "timeout_s": 5,
                    },
                },
                {
                    "user": "Reply with exactly {{marker}} and nothing else.",
                    "expect": {"llm_calls": {"served_model": served_model}},
                },
            ],
        }
    )


def test_http_and_poll_steps_share_saved_values_and_actor_identity() -> None:
    fake = FakeUnibot()
    settings = EvalSettings(base_url="http://unibot.test", concurrency=1, trace_wait_s=0)
    runner = EvalRunner(settings, use_judge=False, transport=fake.transport())
    result = asyncio.run(runner.run([_step_case("fake")], repeats=1))

    attempt = result.cases[0].attempts[0]
    assert attempt.passed, [c.to_dict() for c in attempt.checks if not c.passed]
    method, path, body = fake.api_requests[0]
    assert (method, path) == ("POST", "/model-settings/providers")
    assert body["user_id"].startswith("eval-steps-setup-poll")
    assert fake.api_requests[-1][1] == "/jobs/prov_1"
    assert fake.job_polls == 3
    assert {c.name for c in attempt.checks} >= {"http:POST /model-settings/providers", "poll:items.0.status"}


def test_served_model_check_fails_when_another_model_answered() -> None:
    fake = FakeUnibot(served_model="environment-model")
    settings = EvalSettings(base_url="http://unibot.test", concurrency=1, trace_wait_s=0)
    runner = EvalRunner(settings, use_judge=False, transport=fake.transport())
    attempt = asyncio.run(runner.run([_step_case("selected-model")], repeats=1)).cases[0].attempts[0]

    failed = {c.name: c.detail for c in attempt.checks if not c.passed}
    assert list(failed) == ["llm_calls:served_model:selected-model"]
    assert "environment-model" in failed["llm_calls:served_model:selected-model"]


def test_expected_backend_error_is_a_passing_check_and_unexpected_one_is_not() -> None:
    def case(expect: dict) -> Case:  # type: ignore[type-arg]
        return Case.model_validate({"id": "errors.provider", "turns": [{"user": "FAIL_PROVIDER please", "expect": expect}]})

    settings = EvalSettings(base_url="http://unibot.test", concurrency=1, trace_wait_s=0)
    runner = EvalRunner(settings, use_judge=False, transport=FakeUnibot().transport())
    expected = asyncio.run(runner.run([case({"status": "failed", "error": "HTTP 401"})], repeats=1))
    unexpected = asyncio.run(runner.run([case({})], repeats=1))

    assert expected.cases[0].attempts[0].passed
    assert not unexpected.cases[0].attempts[0].passed
    assert "HTTP 401" in (unexpected.cases[0].attempts[0].error or "")


# ---- long-run features ---------------------------------------------------------------------------------------

DOCS = ["doc-1", "doc-2", "doc-3", "doc-4", "doc-5"]


def _seed_docs(names: list[str] = DOCS) -> list[dict]:  # type: ignore[type-arg]
    return [
        {"action": "http", "request": {"path": "/documents",
                                       "body": {"user_id": "{{user_id}}", "name": name, "content": f"# {name}\n"}}}
        for name in names
    ]


def _doc_state(name: str, **verify: object) -> dict:  # type: ignore[type-arg]
    return {"action": "http", "request": {"method": "GET", "path": f"/documents/{name}.md",
                                          "params": {"user_id": "{{user_id}}"},
                                          "verify": [{"field": "content", **verify}]}}


def _attempt(fake: FakeUnibot, case: dict):  # type: ignore[no-untyped-def,type-arg]
    settings = EvalSettings(base_url="http://unibot.test", concurrency=1, trace_wait_s=0)
    runner = EvalRunner(settings, use_judge=False, transport=fake.transport())
    return asyncio.run(runner.run([Case.model_validate(case)], repeats=1)).cases[0].attempts[0]


def _failed(attempt) -> set[str]:  # type: ignore[no-untyped-def]
    return {check.name for check in attempt.checks if not check.passed}


@pytest.mark.parametrize(
    ("message", "present"),
    [
        ({"role": "user", "content": "Here is a summary of the conversation to date:\n\nticket MK-1"}, True),
        ({"role": "system", "content": "[CONTEXT SUMMARY]\nticket MK-1"}, True),  # legacy runtime
        ({"role": "user", "content": "Summarise this note"}, False),
    ],
)
def test_prompt_summary_is_recognised(message: dict, present: bool) -> None:  # type: ignore[type-arg]
    from unibot_eval.checks import check_prompt
    from unibot_eval.dataset import PromptExpectation

    turn = _turn({})
    turn.llm_calls = [{"request": {"messages": [{"role": "system", "content": "You are Unibot."}, message]}}]
    assert check_prompt(PromptExpectation(has_summary=True), turn)[0].passed is present


def test_counts_and_order_checks() -> None:
    from unibot_eval.checks import check_tools
    from unibot_eval.dataset import ToolExpectation

    trace = {"events": [
        {"kind": "builtin.completed", "target_id": name, "details": {"call_id": f"c{i}"}}
        for i, name in enumerate(["document.search", "document.read", "document.read", "document.update_section"])
    ]}
    spec = ToolExpectation.model_validate({
        "counts": {"document.read": 2, "document.search": {"max": 1}, "document.delete": {"min": 1}},
        "order": [["document.search", "document.update_section"], ["document.update_section", "document.read"]],
    })
    results = {result.name: result.passed for result in check_tools(spec, _turn(trace))}
    assert results == {
        "count:document.read": True,
        "count:document.search": True,
        "count:document.delete": False,
        "order:document.search<document.update_section": True,
        "order:document.update_section<document.read": False,
        "no_failed_calls": True,
    }


FANOUT = {
    "id": "long.fanout",
    "turns": [
        *_seed_docs(),
        {"user": 'Append the line "reviewed {{marker}}" to documents doc-1, doc-2, doc-3, doc-4, doc-5.',
         "expect": {"tools": {"counts": {"document.append": 5}}}},
        *[_doc_state(name, contains=["reviewed {{marker}}"]) for name in DOCS],
        {"action": "http", "request": {"method": "GET", "path": "/documents/doc-9.md",
                                       "params": {"user_id": "{{user_id}}"}, "expect_status": 404}},
    ],
    "final": {"budget": {"max_duplicate_calls": 0}},
}


def test_fanout_state_is_verified_through_the_api() -> None:
    attempt = _attempt(FakeUnibot(), FANOUT)
    assert attempt.passed, [c.to_dict() for c in attempt.checks if not c.passed]
    assert {c.category for c in attempt.checks if c.name.startswith("state:")} == {"state"}
    assert attempt.progress == 1.0


def test_fanout_that_stops_early_fails_count_and_state_checks() -> None:
    attempt = _attempt(FakeUnibot(faults={"stop_early"}), FANOUT)
    failed = _failed(attempt)
    assert "count:document.append" in failed
    assert any(name.startswith("state:content:contains:reviewed") for name in failed)
    assert 0 < attempt.progress < 1


def test_duplicate_work_is_caught_by_final_checks() -> None:
    failed = _failed(_attempt(FakeUnibot(faults={"duplicate_append"}), FANOUT))
    assert "final:budget:duplicate_calls" in failed


# Exactly one "reviewed" line in the document.
ONCE = r"^(?:(?!reviewed).)*reviewed [^\n]+\n(?:(?!reviewed).)*$"

STOP_RESUME = {
    "id": "long.stop_resume",
    "turns": [
        *_seed_docs(),
        {"user": "Remember the release is on Friday.", "expect": {"status": "completed"}},
        {"user": 'Append the line "reviewed {{marker}}" to documents doc-1, doc-2, doc-3, doc-4, doc-5.',
         "stop_after": {"tool_calls": 2},
         "expect": {"status": "stopped", "tools": {"counts": {"document.append": 2}}}},
        {"action": "resume", "expect": {"status": "completed"}},
        *[_doc_state(name, regex=ONCE) for name in DOCS],
    ],
    "final": {"tools": {"counts": {"document.append": 5}}, "budget": {"max_duplicate_calls": 0}},
}


def test_stop_after_tool_calls_then_resume_finishes_without_redoing_work() -> None:
    fake = FakeUnibot()
    attempt = _attempt(fake, STOP_RESUME)
    assert attempt.passed, [c.to_dict() for c in attempt.checks if not c.passed]
    assert len(fake.stop_requests) == 1
    statuses = [turn["status"] for turn in attempt.turns if turn["action"] != "http"]
    assert statuses == ["completed", "stopped", "completed"]


def test_resume_that_repeats_finished_calls_is_caught() -> None:
    failed = _failed(_attempt(FakeUnibot(faults={"redo_on_resume"}), STOP_RESUME))
    assert {"final:count:document.append", "final:budget:duplicate_calls"} <= failed


def test_stop_on_a_new_conversation_creates_it_first() -> None:
    case = {"id": "long.stop_first_turn", "turns": [
        *_seed_docs(DOCS[:3]),
        {"user": 'Append the line "x" to documents doc-1, doc-2, doc-3.', "stop_after": {"tool_calls": 1},
         "expect": {"status": "stopped"}},
    ]}
    fake = FakeUnibot()
    attempt = _attempt(fake, case)
    assert attempt.passed, [c.to_dict() for c in attempt.checks if not c.passed]
    assert fake.stop_requests and fake.stop_requests[0] in fake.deleted


def test_confirm_approval_repeats_until_the_chain_is_done() -> None:
    def case(max_confirmations: int) -> dict:  # type: ignore[type-arg]
        return {"id": "long.approval_chain", "turns": [
            *_seed_docs(DOCS[:3]),
            {"user": "Delete documents doc-1, doc-2, doc-3.", "expect": {"status": "approval_required"}},
            {"action": "confirm_approval", "max_confirmations": max_confirmations,
             "expect": {"tools": {"counts": {"document.delete": 3}}}},
            *[{"action": "http", "request": {"method": "GET", "path": f"/documents/{name}.md",
                                             "params": {"user_id": "{{user_id}}"}, "expect_status": 404}}
              for name in DOCS[:3]],
        ]}

    done = _attempt(FakeUnibot(), case(5))
    assert done.passed, [c.to_dict() for c in done.checks if not c.passed]
    single = _attempt(FakeUnibot(), case(1))
    assert {"status", "count:document.delete"} <= _failed(single)


def test_stop_after_and_final_validation() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Case.model_validate({"id": "x", "turns": [{"user": "hi", "stop_after": {}}]})
    with pytest.raises(ValueError, match="only applies to chat"):
        Case.model_validate({"id": "x", "turns": [{"action": "resume", "stop_after": {"seconds": 1}}]})
    with pytest.raises(ValueError, match="judge"):
        Case.model_validate({"id": "x", "turns": [{"user": "hi"}], "final": {"judge": {"criteria": "c"}}})
    case = Case.model_validate({"id": "x", "turns": [{"user": "hi"}], "final": {"budget": {"max_tool_calls": 1}}})
    assert case.final is not None and case.final.status is None
    assert case.render({}).final.status is None  # type: ignore[union-attr]


def test_report_shows_progress_and_whole_attempt_failures(tmp_path: Path) -> None:
    settings = EvalSettings(base_url="http://unibot.test", concurrency=1, trace_wait_s=0)
    runner = EvalRunner(settings, use_judge=False, transport=FakeUnibot(faults={"duplicate_append"}).transport())
    result = asyncio.run(runner.run([Case.model_validate(FANOUT)], repeats=1))
    markdown = write_reports(result, tmp_path)["markdown"].read_text(encoding="utf-8")
    assert "Progress" in markdown and "whole attempt · `final:budget:duplicate_calls`" in markdown
    assert 0 < summarize(result)["overall"]["mean_progress"] < 1


# ---- --show-in-ui --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("auth_user", "expected"),
    [
        (None, ("anonymous", "default")),  # no auth: the UI acts as the local anonymous user
        (("user_dev", "default"), ("user_dev", "default")),  # dev auth bypass (or a session cookie)
        (("", ""), None),  # login required and these requests carry none
    ],
)
def test_request_user_is_the_user_the_ui_shows(auth_user, expected) -> None:  # type: ignore[no-untyped-def]
    from unibot_eval.client import UnibotEvalClient

    async def lookup():  # type: ignore[no-untyped-def]
        async with UnibotEvalClient("http://unibot.test", transport=FakeUnibot(auth_user=auth_user).transport()) as c:
            return await c.request_user()

    assert asyncio.run(lookup()) == expected


def test_show_in_ui_runs_as_the_ui_user_and_keeps_titled_conversations() -> None:
    fake = FakeUnibot(auth_user=("user_dev", "default"))
    settings = EvalSettings(base_url="http://unibot.test", concurrency=1, trace_wait_s=0, ui_user_id="user_dev",
                            keep_conversations=True)
    case = Case.model_validate({"id": "ui.case", "turns": [
        *_seed_docs(DOCS[:1]),
        {"user": "Reply with exactly {{marker}} and nothing else."},
        {"user": "What was the code?"},
        {"user": "Reply with exactly {{marker2}} and nothing else.", "new_conversation": True},
    ]})
    runner = EvalRunner(settings, use_judge=False, transport=fake.transport())
    attempt = asyncio.run(runner.run([case], repeats=1)).cases[0].attempts[0]

    assert attempt.passed, [c.to_dict() for c in attempt.checks if not c.passed]
    assert ("user_dev", "doc-1.md") in fake.documents
    titled = {c["title"]: c for c in fake.conversations.values() if c.get("title")}
    assert set(titled) == {"[eval] ui.case #1", "[eval] ui.case #1 · conversation 2"}
    assert all(c["user"] == "user_dev" for c in titled.values())
    assert len(titled["[eval] ui.case #1"]["messages"]) == 4  # both turns landed in the titled conversation
    assert not fake.deleted


def test_show_in_ui_skips_cases_that_need_several_users() -> None:
    from unibot_eval.cli import split_single_user

    cases = select_cases(load_cases([DATASETS]), ids=["context.cross_user_isolation", "context.recall_previous_turn"])
    kept, skipped = split_single_user(cases)
    assert [c.id for c in kept] == ["context.recall_previous_turn"]
    assert [c.id for c in skipped] == ["context.cross_user_isolation"]
