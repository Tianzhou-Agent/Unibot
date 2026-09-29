from __future__ import annotations

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

import httpx
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.language_models.chat_models import BaseChatModel

from benchmarks.cases import ACTOR, Case
from benchmarks.environment import BenchmarkWorld, grade_trial
from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.model_providers.factory import create_model_from_settings
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.sandbox.factory import create_sandbox_service
from tianzhou_agent_platform.store.nas.filesystem import NasStore


class UsageMeter(AsyncCallbackHandler):
    """Native callback recording the usage of every model call the benchmarked model makes."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._open: dict[Any, tuple[dict[str, Any], float]] = {}

    async def on_chat_model_start(
        self, serialized: dict[str, Any], messages: Any, *, run_id: Any, metadata: dict[str, Any] | None = None, **_: Any
    ) -> None:
        metadata = metadata or {}
        context_type = metadata.get("context_type") or (
            "compression" if metadata.get("lc_source") == "summarization" else None
        )
        call = {"status": "failed", "input_tokens": None, "output_tokens": None,
                "usage_estimated": False, "context_type": context_type}
        self.calls.append(call)
        self._open[run_id] = (call, perf_counter())

    async def on_llm_end(self, response: Any, *, run_id: Any, **_: Any) -> None:
        call, start = self._open.pop(run_id, ({}, perf_counter()))
        message = response.generations[0][0].message
        usage = getattr(message, "usage_metadata", None)
        call.update(status="completed", input_tokens=usage["input_tokens"] if usage else None,
                    output_tokens=usage["output_tokens"] if usage else None, usage_estimated=usage is None,
                    finish_reason=message.response_metadata.get("finish_reason"),
                    duration_ms=(perf_counter() - start) * 1000)

    async def on_llm_error(self, error: BaseException, *, run_id: Any, **_: Any) -> None:
        call, start = self._open.pop(run_id, ({}, perf_counter()))
        call["duration_ms"] = (perf_counter() - start) * 1000


def token_cost(calls: list[dict[str, Any]], rates: tuple[float, float] | None) -> dict[str, Any]:
    complete = all(call["status"] == "completed" for call in calls)
    inputs = sum(call["input_tokens"] or 0 for call in calls)
    outputs = sum(call["output_tokens"] or 0 for call in calls)
    return {
        "known_input_tokens": inputs, "known_output_tokens": outputs,
        "usage_complete": complete,
        "usage_estimated": any(call["usage_estimated"] for call in calls),
        "estimated_cost_usd": ((inputs * rates[0] + outputs * rates[1]) / 1_000_000) if complete and rates else None,
    }


async def run_trial(
    case: Case, settings: AgentSettings, trial: int, work_root: Path, *,
    timeout_seconds: float = 180, rates: tuple[float, float] | None = None, llm: BaseChatModel | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "case_id": case.id, "category": case.category, "trial": trial,
        "prompts": [turn.prompt for turn in case.turns], "responses": [], "traces": [], "error": None,
    }
    work_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="trial-", dir=work_root) as directory:
        trial_root = Path(directory)
        isolated_settings = settings.model_copy(update={
            "obs_enabled": False, "sandbox_driver": "local", "sandbox_allow_unsafe_local": False,
            "sandbox_workspace_root": trial_root / "sandbox", "admin_identities": "",
        })
        repository = InMemoryRepository()
        documents = DocumentService(NasStore(trial_root))
        world = BenchmarkWorld(case, repository, documents)
        await world.seed()
        record.update(initial_state=await world.snapshot(), memory_id=world.memory_id)
        meter = UsageMeter()
        model = llm or create_model_from_settings(
            model=isolated_settings.llm_model or "",
            api_key=isolated_settings.llm_api_key.get_secret_value() if isolated_settings.llm_api_key else None,
            base_url=isolated_settings.llm_base_url,
            timeout_seconds=isolated_settings.llm_timeout_seconds,
        )
        model = model.model_copy(update={"callbacks": [meter]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(world.handle)) as connectors:
            app = create_app(
                settings=isolated_settings, repository=repository, llm=model,
                capability_http_client=connectors, vision_http_client=connectors, document_service=documents,
                sandbox_service=create_sandbox_service(isolated_settings, repository, enforce_isolation=True),
            )
            async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://benchmark.invalid",
            ) as client:
                conversation_id = None
                trace_ids: set[str] = set()
                start = perf_counter()
                try:
                    async with asyncio.timeout(timeout_seconds):
                        for turn in case.turns:
                            response = await client.post("/chat", json={
                                **ACTOR, "message": turn.prompt,
                                "conversation_id": None if turn.new_conversation else conversation_id,
                            })
                            response.raise_for_status()
                            body = response.json()
                            record["responses"].append(body)
                            conversation_id = body["conversation_id"]
                            trace_ids.add(body["trace_id"])
                            if body["status"] == "approval_required":
                                if case.approval is None:
                                    break
                                record["approval"] = {"state_before": await world.snapshot(), "action": case.approval}
                                resolution = await client.post(
                                    f"/approvals/{body['approval']['id']}/{case.approval}", json=ACTOR,
                                )
                                resolution.raise_for_status()
                                resolved = resolution.json()
                                record["approval"]["response"] = resolved
                                record["approval"]["resolved"] = resolved["status"] == (
                                    "completed" if case.approval == "confirm" else "denied"
                                )
                                if case.approval == "confirm":
                                    record["responses"][-1] = resolved
                            elif body["status"] != "completed":
                                break
                except TimeoutError:
                    record["error"] = {"kind": "timeout"}
                except Exception as exc:
                    # Error bodies may contain provider credentials or URLs; keep a diagnostic class/code only.
                    record["error"] = {"kind": "execution_error", "type": type(exc).__name__}
                    if isinstance(exc, PlatformError):
                        record["error"]["code"] = exc.code
                    if isinstance(exc, httpx.HTTPStatusError):
                        record["error"]["http_status"] = exc.response.status_code
                finally:
                    record["duration_ms"] = (perf_counter() - start) * 1000
                    # Runtime failures can occur before /chat returns a trace ID. Retain those runs as well.
                    for conversation in await repository.list_conversations(**ACTOR):
                        for trace in await repository.list_conversation_traces(conversation.id):
                            trace_ids.add(trace.trace_id)
                    record["traces"] = [
                        (await repository.get_trace(trace_id)).model_dump(mode="json") for trace_id in sorted(trace_ids)
                    ]
                    record["final_state"] = await world.snapshot()
        record["connector_requests"] = world.requests
        record["model_calls"] = meter.calls
        record.update(token_cost(meter.calls, rates))
        record["checks"] = grade_trial(case, record)
        record["passed"] = all(record["checks"].values())
        record["failure_class"] = (
            None if record["passed"] else record["error"]["kind"] if record["error"] else
            "agent_failed" if any(item.get("status") == "failed" for item in record["responses"]) else "grading_failed"
        )
        return record
