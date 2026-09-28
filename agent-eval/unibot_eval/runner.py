"""Runs dataset cases against the backend k times, scores every turn and aggregates pass@k / pass^k."""

from __future__ import annotations

import asyncio
import re
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from opentelemetry.trace import Status, StatusCode

from unibot_eval.checks import PROTOCOL, CheckResult, run_checks
from unibot_eval.client import BackendError, ChatResult, UnibotEvalClient
from unibot_eval.config import EvalSettings
from unibot_eval.dataset import Case, HttpStep, PollStep, Turn, dig, fresh_vars, render_runtime
from unibot_eval.judge import LLMJudge
from unibot_eval.telemetry import set_attributes, tracer
from unibot_eval.trajectory import TurnObservation, extract_tool_calls


@dataclass
class AttemptResult:
    case_id: str
    attempt: int
    passed: bool
    checks: list[CheckResult]
    turns: list[dict[str, Any]]
    duration_ms: float
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "attempt": self.attempt,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "notes": self.notes,
            "checks": [check.to_dict() for check in self.checks],
            "turns": self.turns,
        }


@dataclass
class CaseResult:
    case: Case
    attempts: list[AttemptResult]

    @property
    def k(self) -> int:
        return len(self.attempts)

    @property
    def pass_rate(self) -> float:
        return sum(attempt.passed for attempt in self.attempts) / self.k if self.k else 0.0

    @property
    def pass_at_k(self) -> bool:
        """Succeeded at least once in k attempts (capability)."""
        return any(attempt.passed for attempt in self.attempts)

    @property
    def pass_hat_k(self) -> bool:
        """Succeeded in every one of k attempts (reliability — what users experience)."""
        return bool(self.attempts) and all(attempt.passed for attempt in self.attempts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case.id,
            "description": self.case.description,
            "tags": self.case.tags,
            "source": self.case.source,
            "k": self.k,
            "pass_rate": self.pass_rate,
            "pass_at_k": self.pass_at_k,
            "pass_hat_k": self.pass_hat_k,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
        }


@dataclass
class RunResult:
    cases: list[CaseResult]
    started_at: str
    finished_at: str
    settings: dict[str, Any]



class EvalRunner:
    def __init__(
        self,
        settings: EvalSettings,
        *,
        judge: LLMJudge | None = None,
        use_judge: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._judge = judge
        if self._judge is None and use_judge and settings.judge.configured:
            self._judge = LLMJudge.from_settings(settings.judge)
        self._use_judge = use_judge
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop: asyncio.AbstractEventLoop | None = None

    def _client(self) -> UnibotEvalClient:
        return UnibotEvalClient(
            self.settings.base_url,
            timeout_s=self.settings.timeout_s,
            headers=self.settings.headers,
            transport=self._transport,
        )

    async def run(self, cases: list[Case], *, repeats: int | None = None) -> RunResult:
        started = _now()
        with tracer().start_as_current_span("eval.run") as span:
            set_attributes(span, {"eval.case_count": len(cases), "eval.base_url": self.settings.base_url})
            results = await asyncio.gather(*(self.run_case(case, repeats=repeats) for case in cases))
            passed = sum(result.pass_hat_k for result in results)
            set_attributes(span, {"eval.pass_hat_k_cases": passed})
        return RunResult(
            cases=list(results),
            started_at=started,
            finished_at=_now(),
            settings={
                "base_url": self.settings.base_url,
                "stream": self.settings.stream,
                "judge_model": self.settings.judge.model if self._judge and self._use_judge else None,
            },
        )

    async def run_case(self, case: Case, *, repeats: int | None = None) -> CaseResult:
        k = repeats or case.repeats or self.settings.repeats
        with tracer().start_as_current_span("eval.case") as span:
            set_attributes(span, {"eval.case_id": case.id, "eval.tags": case.tags, "eval.k": k})
            attempts = await asyncio.gather(*(self._guarded_attempt(case, number) for number in range(1, k + 1)))
            result = CaseResult(case=case, attempts=list(attempts))
            set_attributes(
                span,
                {"eval.pass_rate": result.pass_rate, "eval.pass_at_k": result.pass_at_k,
                 "eval.pass_hat_k": result.pass_hat_k},
            )
            if not result.pass_hat_k:
                span.set_status(Status(StatusCode.ERROR, "case not reliable (pass^k failed)"))
        return result

    async def _guarded_attempt(self, case: Case, number: int) -> AttemptResult:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._semaphore_loop is not loop:  # one limiter per event loop
            self._semaphore = asyncio.Semaphore(max(1, self.settings.concurrency))
            self._semaphore_loop = loop
        async with self._semaphore:
            return await self.run_attempt(case, number)

    async def run_attempt(self, case: Case, number: int) -> AttemptResult:
        rendered = case.render(fresh_vars(case))
        run_tag = secrets.token_hex(3)
        actors: dict[str, str] = {}
        conversations: dict[str, str | None] = {}
        created: set[str] = set()
        observations: list[TurnObservation] = []
        checks: list[CheckResult] = []
        notes: list[str] = []
        saved: dict[str, str] = {}  # values saved by http steps, available to later steps as {{name}}
        error: str | None = None
        started = time.perf_counter()
        with tracer().start_as_current_span("eval.attempt") as span:
            set_attributes(span, {"eval.case_id": case.id, "eval.attempt": number})
            async with self._client() as client:
                try:
                    for index, turn in enumerate(rendered.turns):
                        user_id = actors.setdefault(turn.actor, _actor_id(case.id, turn.actor, run_tag))
                        step_checks: list[CheckResult] = []
                        if turn.action in {"http", "poll"}:
                            observation, step_checks = await self._run_step(client, index, turn, user_id, saved)
                        else:
                            try:
                                observation = await self._run_turn(
                                    client, index, turn, user_id, conversations, observations
                                )
                            except BackendError as exc:
                                if turn.expect.error is None:
                                    raise
                                observation = TurnObservation(
                                    index=index,
                                    actor=turn.actor,
                                    user_id=user_id,
                                    action=turn.action,
                                    input=turn.user,
                                    response={"status": "failed", "content": "", "error": str(exc)},
                                )
                        if observation.conversation_id:
                            created.add(observation.conversation_id)
                        observations.append(observation)
                        turn_checks = step_checks + run_checks(turn.expect, observation)
                        turn_checks += await self._judge_turn(turn, observation, observations, notes)
                        checks.extend(turn_checks)
                        _record_checks(turn_checks)
                except (BackendError, httpx.HTTPError, KeyError, ValueError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
                    checks.append(
                        CheckResult("request", PROTOCOL, False, 0.0, error, turn=len(observations))
                    )
                finally:
                    if not self.settings.keep_conversations:
                        for conversation_id in created:
                            try:
                                await client.delete_conversation(conversation_id)
                            except (BackendError, httpx.HTTPError):
                                notes.append(f"cleanup failed for {conversation_id}")
            passed = error is None and all(check.passed for check in checks)
            set_attributes(span, {"eval.passed": passed, "eval.check_count": len(checks)})
            if not passed:
                span.set_status(Status(StatusCode.ERROR, error or "checks failed"))
        return AttemptResult(
            case_id=case.id,
            attempt=number,
            passed=passed,
            checks=checks,
            turns=[observation.summary() for observation in observations],
            duration_ms=(time.perf_counter() - started) * 1000,
            error=error,
            notes=notes,
        )

    async def _run_step(
        self,
        client: UnibotEvalClient,
        index: int,
        turn: Turn,
        user_id: str,
        saved: dict[str, str],
    ) -> tuple[TurnObservation, list[CheckResult]]:
        """Run an API setup (``http``) or background-job (``poll``) step and check its outcome."""
        variables = {**saved, "user_id": user_id, "tenant_id": self.settings.tenant_id}
        checks: list[CheckResult] = []
        started = time.perf_counter()
        with tracer().start_as_current_span("eval.step") as span:
            set_attributes(span, {"eval.turn": index, "eval.actor": turn.actor, "eval.action": turn.action})
            if turn.action == "http":
                assert turn.request is not None
                request = HttpStep.model_validate(render_runtime(turn.request.model_dump(), variables))
                unresolved = re.findall(r"\{\{\s*([\w.|]+)\s*\}\}", request.model_dump_json())
                if unresolved:
                    raise ValueError(f"turn {index}: unresolved placeholders {sorted(set(unresolved))}")
                status, body = await client.request(
                    request.method, request.path, body=request.body, params=request.params
                )
                ok = 200 <= status < 300
                checks.append(
                    CheckResult(
                        f"http:{request.method} {request.path}",
                        PROTOCOL,
                        ok,
                        1.0 if ok else 0.0,
                        f"HTTP {status}" + ("" if ok else f": {str(body)[:300]}"),
                    )
                )
                for name, path in request.save.items():
                    value = dig(body, path)
                    if value is None:
                        checks.append(CheckResult(f"save:{name}", PROTOCOL, False, 0.0, f"{path} not in response"))
                    else:
                        saved[name] = str(value)
                summary = f"{request.method} {request.path} -> HTTP {status}"
            else:
                assert turn.poll is not None
                poll = PollStep.model_validate(render_runtime(turn.poll.model_dump(), variables))
                deadline = time.monotonic() + poll.timeout_s
                while True:
                    status, body = await client.request("GET", poll.path, params=poll.params)
                    value = dig(body, poll.field) if 200 <= status < 300 else None
                    if value in poll.until or time.monotonic() >= deadline:
                        break
                    await asyncio.sleep(poll.interval_s)
                expected = poll.equals if poll.equals is not None else poll.until[0]
                waited = time.perf_counter() - started
                ok = value == expected
                checks.append(
                    CheckResult(
                        f"poll:{poll.field}",
                        PROTOCOL,
                        ok,
                        1.0 if ok else 0.0,
                        f"{poll.field}={value!r} (HTTP {status}) after {waited:.0f}s; expected {expected!r}",
                    )
                )
                summary = f"GET {poll.path}: {poll.field}={value!r}"
        for check in checks:
            check.turn = index
        observation = TurnObservation(
            index=index,
            actor=turn.actor,
            user_id=user_id,
            action=turn.action,
            input=summary,
            response={"content": summary},
            latency_ms=(time.perf_counter() - started) * 1000,
            ttft_ms=None,
        )
        return observation, checks

    async def _run_turn(
        self,
        client: UnibotEvalClient,
        index: int,
        turn: Turn,
        user_id: str,
        conversations: dict[str, str | None],
        previous: list[TurnObservation],
    ) -> TurnObservation:
        tenant_id = self.settings.tenant_id
        with tracer().start_as_current_span("eval.turn") as span:
            set_attributes(span, {"eval.turn": index, "eval.actor": turn.actor, "eval.action": turn.action})
            if turn.action == "chat":
                if turn.new_conversation:
                    conversations[turn.actor] = None
                payload = {
                    "message": turn.user,
                    "conversation_id": conversations.get(turn.actor),
                    "user_id": user_id,
                    "tenant_id": tenant_id,
                    "capability": turn.capability,
                    "preferred_aina_id": turn.preferred_aina_id,
                    "ui_context": turn.ui_context,
                }
                result = await client.chat(payload, stream=self.settings.stream)
                conversations[turn.actor] = result.response.get("conversation_id")
            else:
                approval = _pending_approval(previous, turn.actor)
                if approval is None:
                    raise ValueError(f"turn {index}: no pending approval for actor {turn.actor!r}")
                if turn.action == "confirm_approval":
                    result = await client.confirm_approval(approval["id"], user_id=user_id, tenant_id=tenant_id)
                else:
                    record = await client.deny_approval(approval["id"], user_id=user_id, tenant_id=tenant_id)
                    result = ChatResult(
                        response={
                            "status": record.get("status"),
                            "trace_id": record.get("trace_id"),
                            "conversation_id": record.get("conversation_id"),
                            "content": "",
                        },
                        latency_ms=0.0,
                        ttft_ms=None,
                    )

            observation = TurnObservation(
                index=index,
                actor=turn.actor,
                user_id=user_id,
                action=turn.action,
                input=turn.user,
                response=result.response,
                latency_ms=result.latency_ms,
                ttft_ms=result.ttft_ms,
            )
            trace_id = observation.trace_id
            if trace_id:
                observation.trace = await client.get_trace(trace_id, wait_s=self.settings.trace_wait_s)
                observation.tool_calls = extract_tool_calls(observation.trace)
                try:
                    observation.llm_calls = await client.llm_calls_for_trace(
                        trace_id, user_id=user_id, tenant_id=tenant_id
                    )
                except (BackendError, httpx.HTTPError):
                    observation.llm_calls = []
            set_attributes(
                span,
                {
                    "unibot.trace_id": trace_id,
                    "gen_ai.conversation.id": observation.conversation_id,
                    "unibot.status": observation.status,
                    "eval.latency_ms": observation.latency_ms,
                    "eval.ttft_ms": observation.ttft_ms,
                    "gen_ai.usage.input_tokens": observation.input_tokens,
                    "gen_ai.usage.output_tokens": observation.output_tokens,
                    "eval.tool_calls": [call.name for call in observation.tool_calls],
                },
            )
            return observation

    async def _judge_turn(
        self,
        turn: Turn,
        observation: TurnObservation,
        observations: list[TurnObservation],
        notes: list[str],
    ) -> list[CheckResult]:
        if not turn.expect.judge:
            return []
        if self._judge is None or not self._use_judge:
            notes.append(f"turn {observation.index}: {len(turn.expect.judge)} judge check(s) skipped (judge disabled)")
            return []
        history = [item for item in observations[:-1] if item.actor == observation.actor]
        return list(
            await asyncio.gather(*(self._judge.evaluate(spec, observation, history) for spec in turn.expect.judge))
        )


def _pending_approval(previous: list[TurnObservation], actor: str) -> dict[str, Any] | None:
    for observation in reversed(previous):
        if observation.actor == actor and observation.approval:
            return observation.approval
    return None


def _record_checks(checks: list[CheckResult]) -> None:
    from opentelemetry import trace

    span = trace.get_current_span()
    for check in checks:
        span.add_event(
            "eval.check",
            {"check.name": check.name, "check.category": check.category, "check.passed": check.passed,
             "check.score": check.score, "check.turn": check.turn if check.turn is not None else -1},
        )


def _actor_id(case_id: str, label: str, run_tag: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", case_id.lower()).strip("-")[:32]
    return f"eval-{slug}-{label}-{run_tag}"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
