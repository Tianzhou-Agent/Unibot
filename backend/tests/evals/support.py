from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from deepeval.evaluate import assert_test
from deepeval.metrics import GEval, StepEfficiencyMetric, TaskCompletionMetric, ToolCorrectnessMetric
from deepeval.models import GPTModel
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall, ToolCallParams

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.sdk import UnibotClient

EVAL_BASE_URL = os.getenv("UNIBOT_EVAL_BASE_URL")
REAL_EVAL_MARK = pytest.mark.skipif(
    not EVAL_BASE_URL,
    reason="Set UNIBOT_EVAL_BASE_URL to run real Agent evaluations.",
)


@dataclass(frozen=True)
class AgentRun:
    input: str
    response: dict[str, Any]
    trace: dict[str, Any]


def eval_base_url() -> str:
    if EVAL_BASE_URL is None:
        raise RuntimeError("UNIBOT_EVAL_BASE_URL is required")
    return EVAL_BASE_URL


def eval_actor(prefix: str) -> tuple[str, str]:
    suffix = uuid4().hex[:12]
    return f"eval-{prefix}-{suffix}", "deepeval"


def judge_model() -> GPTModel:
    settings = AgentSettings()
    configured_key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
    return GPTModel(
        model=_required(os.getenv("DEEPEVAL_JUDGE_MODEL") or settings.llm_model, "DEEPEVAL_JUDGE_MODEL"),
        api_key=_required(os.getenv("DEEPEVAL_JUDGE_API_KEY") or configured_key, "DEEPEVAL_JUDGE_API_KEY"),
        base_url=_required(
            os.getenv("DEEPEVAL_JUDGE_BASE_URL") or settings.llm_base_url,
            "DEEPEVAL_JUDGE_BASE_URL",
        ),
        temperature=0,
    )


async def chat_run(
    client: UnibotClient,
    message: str,
    *,
    conversation_id: str | None = None,
    user_id: str = "anonymous",
    tenant_id: str = "default",
    capability: str | None = None,
) -> AgentRun:
    response = await client.chat(
        message,
        conversation_id=conversation_id,
        user_id=user_id,
        tenant_id=tenant_id,
        capability=capability,
    )
    trace = await client.get_trace(response["trace_id"])
    return AgentRun(input=message, response=response, trace=trace)


async def delete_conversations(client: UnibotClient, conversation_ids: set[str]) -> None:
    for conversation_id in conversation_ids:
        await client.delete_conversation(conversation_id)


def assert_agent_run(
    run: AgentRun,
    *,
    task: str,
    expected_output: str,
    expected_tools: list[str] | list[ToolCall] | None,
    criteria: str,
    completion_threshold: float = 0.7,
    efficiency_threshold: float = 0.6,
    evaluate_correctness: bool = True,
    evaluate_efficiency: bool = True,
) -> None:
    case = LLMTestCase(
        name=task,
        input=run.input,
        actual_output=str(run.response["content"]).strip(),
        expected_output=expected_output,
        tools_called=_completed_tools(run.trace),
        expected_tools=(
            [ToolCall(name=item) if isinstance(item, str) else item for item in expected_tools]
            if expected_tools is not None
            else None
        ),
    )
    setattr(case, "_trace_dict", _deepeval_trace(run))
    judge = judge_model()
    metrics: list[Any] = [
        TaskCompletionMetric(
            threshold=completion_threshold,
            task=task,
            model=judge,
            async_mode=False,
        ),
    ]
    if evaluate_efficiency:
        metrics.append(
            StepEfficiencyMetric(
                threshold=efficiency_threshold,
                model=judge,
                async_mode=False,
            )
        )
    if evaluate_correctness:
        metrics.append(
            GEval(
                name=f"{task} correctness",
                criteria=criteria,
                evaluation_steps=[
                    "Identify the factual and behavioral requirements stated by the criteria and expected output.",
                    "Verify that the actual output satisfies every requirement without contradiction or fabrication.",
                    "Allow equivalent wording and Markdown formatting unless the criteria explicitly requires exact text.",
                ],
                evaluation_params=[
                    SingleTurnParams.INPUT,
                    SingleTurnParams.ACTUAL_OUTPUT,
                    SingleTurnParams.EXPECTED_OUTPUT,
                ],
                threshold=0.7,
                model=judge,
                async_mode=False,
            )
        )
    if expected_tools is not None:
        metrics.insert(
            0,
            ToolCorrectnessMetric(
                threshold=1,
                should_exact_match=True,
                evaluation_params=(
                    [ToolCallParams.INPUT_PARAMETERS, ToolCallParams.OUTPUT]
                    if expected_tools and all(isinstance(item, ToolCall) for item in expected_tools)
                    else []
                ),
                model=judge,
                async_mode=False,
            ),
        )
    assert_test(case, metrics=metrics, run_async=False)


def _required(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is required for DeepEval judge metrics")
    return value


def _completed_tools(trace: dict[str, Any]) -> list[ToolCall]:
    completed_kinds = {"tool.completed", "aina.completed", "builtin.completed"}
    arguments_by_call = {
        event["details"]["call_id"]: event["details"]["arguments"]
        for event in trace["events"]
        if event.get("kind") in {"tool.requested", "aina.requested", "builtin.requested"}
        and "call_id" in event.get("details", {}) and "arguments" in event["details"]
    }
    spans_by_call = {
        span["logical_call_id"]: span
        for span in trace.get("spans", []) if span.get("logical_call_id")
    }
    tools = []
    for event in trace["events"]:
        if event.get("kind") not in completed_kinds or not event.get("target_id"):
            continue
        details = event.get("details", {})
        call_id = details.get("call_id")
        span = spans_by_call.get(call_id, {})
        tools.append(ToolCall(
            name=str(event["target_id"]),
            input_parameters=arguments_by_call.get(call_id, span.get("input")),
            output=details.get("result", span.get("output")),
        ))
    return tools


def _deepeval_trace(run: AgentRun) -> dict[str, Any]:
    # Preserve observations, including failures and repeated calls. Whether a step
    # was necessary is the grader's decision, never an annotation from the agent.
    steps = [
        {"name": event["kind"], **event}
        for event in run.trace["events"]
    ]
    return {
        "name": "unibot-agent-run",
        "input": run.input,
        "output": str(run.response["content"]).strip(),
        "status": run.trace["status"],
        "iterations": run.response["iterations"],
        "runtime_contract": (
            "Events and spans are two views of the same run, linked by call ID; do not count them twice. "
            "Requested and completed events describe one attempt. Routing activates a capability scope. "
            "Evaluate necessity, repeated attempts, arguments, results, and failures against the user's task. "
            "Approval is a platform prerequisite for high-risk operations, but the operation itself may be unnecessary."
        ),
        "steps": steps,
        "spans": run.trace.get("spans", []),
    }
