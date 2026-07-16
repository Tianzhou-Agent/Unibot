from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

import pytest
from deepeval.evaluate import assert_test
from deepeval.metrics import GEval, StepEfficiencyMetric, TaskCompletionMetric, ToolCorrectnessMetric
from deepeval.models import GPTModel
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall

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
    expected_tools: list[str] | None,
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
            [ToolCall(name=name, input_parameters={}) for name in expected_tools]
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
    return [
        ToolCall(name=str(event["target_id"]), input_parameters={})
        for event in trace["events"]
        if event.get("kind") in completed_kinds and event.get("target_id")
    ]


def _deepeval_trace(run: AgentRun) -> dict[str, Any]:
    steps: list[dict[str, Any]] = []
    for event in run.trace["events"]:
        kind = event["kind"]
        details = event.get("details", {})
        if kind == "model.completed":
            tool_call_count = int(details.get("tool_call_count", 0))
            steps.append(
                {
                    "name": (
                        "Construct the required validated capability call"
                        if tool_call_count
                        else "Compose the final user-facing answer"
                    ),
                    "type": "model",
                    "iteration": details.get("iteration"),
                    "tool_call_count": tool_call_count,
                    "required": True,
                    "required_reason": (
                        "The model must translate the natural-language request into the capability's JSON arguments."
                        if tool_call_count
                        else "The model must translate the structured capability result into the final answer."
                    ),
                }
            )
        elif kind == "routing.aina.completed":
            steps.append(
                {
                    "name": "Route to the matching AINA",
                    "type": "routing",
                    "matched_aina": event.get("target_id"),
                    "required": True,
                    "required_reason": "Automatic AINA selection requires a routing decision before invocation.",
                }
            )
        elif kind in {"tool.completed", "aina.completed", "builtin.completed"}:
            target_id = str(event.get("target_id") or "capability")
            required_reason = "This capability performs the operation requested by the user."
            if target_id == "request_clarification":
                required_reason = (
                    "Only request_clarification can create the required host-rendered interactive form; "
                    "plain text cannot satisfy a form request."
                )
            steps.append(
                {
                    "name": f"Execute {target_id}",
                    "type": event.get("target_type"),
                    "status": "completed",
                    "required": True,
                    "required_reason": required_reason,
                }
            )
        elif kind in {"tool.failed", "aina.failed", "builtin.failed"}:
            steps.append(
                {
                    "name": f"Handle failure from {event.get('target_id')}",
                    "type": event.get("target_type"),
                    "status": "failed",
                    "code": details.get("code"),
                }
            )
        elif kind.startswith("approval."):
            steps.append(
                {
                    "name": kind,
                    "type": "approval",
                    "status": event["status"],
                    "required": True,
                    "required_reason": "The platform requires approval for this high-risk operation.",
                }
            )
    return {
        "name": "unibot-agent-run",
        "input": run.input,
        "output": str(run.response["content"]).strip(),
        "status": run.trace["status"],
        "iterations": run.response["iterations"],
        "runtime_contract": (
            "A model call is not a Tool or AINA capability call. A capability workflow requires one model "
            "step to select the capability and, after execution, one model step to convert its result into a "
            "user-facing answer. Those are distinct required steps. Routing and capability discovery metadata "
            "are not remote capability executions. memory.forget and every high-risk capability must pass the "
            "approval gate before execution; that approval is a mandatory safety step, never optional overhead. "
            "A host-rendered form can only be produced by request_clarification, and a remote Tool can only run "
            "after the model constructs schema-valid JSON arguments."
        ),
        "steps": steps,
    }
