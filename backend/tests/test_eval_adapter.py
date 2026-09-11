from copy import deepcopy
import json

import pytest
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import ToolCall

from tests.evals import support


class NoNetworkJudge(DeepEvalBaseLLM):
    def load_model(self):
        return None

    def get_model_name(self):
        return "offline-test-judge"

    def generate(self, *args, **kwargs):
        raise AssertionError("The deterministic tool grader must not call a model")

    async def a_generate(self, *args, **kwargs):
        raise AssertionError("The deterministic tool grader must not call a model")


def _trace():
    return {
        "status": "completed",
        "events": [
            {"kind": "model.completed", "status": "completed", "details": {"iteration": 1}},
            {"kind": "tool.requested", "status": "started", "target_id": "add",
             "details": {"call_id": "call-1", "arguments": {"a": 17, "b": 25}}},
            {"kind": "tool.completed", "status": "completed", "target_id": "add",
             "details": {"call_id": "call-1", "result": {"result": 42}}},
        ],
        "spans": [{"logical_call_id": "call-1", "input": {"a": 17, "b": 25}, "output": {"result": 42}}],
    }


@pytest.mark.parametrize("mutation", ["none", "arguments", "output", "missing", "duplicate", "failed"])
def test_tool_grader_rejects_incorrect_runs_without_model_calls(monkeypatch, mutation):
    trace = _trace()
    if mutation == "arguments":
        trace["events"][1]["details"]["arguments"]["a"] = 99
    elif mutation == "output":
        trace["events"][2]["details"]["result"]["result"] = 124
    elif mutation == "missing":
        trace["events"].pop()
    elif mutation == "duplicate":
        trace["events"].append(deepcopy(trace["events"][2]))
    elif mutation == "failed":
        trace["events"][2].update(kind="tool.failed", status="failed")
    monkeypatch.setattr(support, "judge_model", NoNetworkJudge)

    def check(case, *, metrics, **kwargs):
        score = metrics[0].measure(case, _show_indicator=False, _log_metric_to_confident=False)
        assert score == (1 if mutation == "none" else 0)

    monkeypatch.setattr(support, "assert_test", check)
    support.assert_agent_run(
        support.AgentRun("Add 17 and 25", {"content": "42", "iterations": 1}, trace),
        task="addition", expected_output="42", criteria="Correct sum",
        expected_tools=[ToolCall(name="add", input_parameters={"a": 17, "b": 25}, output={"result": 42})],
        evaluate_correctness=False, evaluate_efficiency=False,
    )


def test_efficiency_trace_preserves_redundancy_failures_and_evidence_without_endorsing_steps():
    trace = _trace()
    trace["events"].extend([
        deepcopy(trace["events"][2]),
        {"kind": "tool.failed", "status": "failed", "target_id": "add",
         "details": {"call_id": "call-2", "code": "TIMEOUT"}},
        {"kind": "approval.required", "status": "pending", "details": {"approval_id": "approval-1"}},
    ])
    original = deepcopy(trace)
    adapted = support._deepeval_trace(support.AgentRun("Add 17 and 25", {"content": "42", "iterations": 2}, trace))
    assert len(adapted["steps"]) == len(trace["events"])
    assert [step["name"] for step in adapted["steps"]].count("tool.completed") == 2
    assert adapted["spans"] == trace["spans"]
    assert adapted["steps"][1]["details"]["arguments"] == {"a": 17, "b": 25}
    assert '"required": true' not in json.dumps(adapted)
    assert "required_reason" not in json.dumps(adapted)
    assert trace == original


def test_missing_tool_arguments_remain_unknown_instead_of_becoming_empty_arguments():
    trace = _trace()
    trace["events"].pop(1)
    trace["spans"] = []
    assert support._completed_tools(trace)[0].input_parameters is None
