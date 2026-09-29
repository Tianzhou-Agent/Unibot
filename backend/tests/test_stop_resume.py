"""Stopping a running turn and resuming it from its LangGraph checkpoint."""

from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tianzhou_agent_platform.aina.memory.models import MemoryRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.services.agent_integration.runner import STOPPED_TOOL_RESULT
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository
from tests.support.fake_llm import LLMResult, ScriptedLLM, assistant


def _settings() -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model="test-model",
    )


class FirstCallBlocksLLM(ScriptedLLM):
    """Blocks its first request until the run is cancelled; later requests follow the script."""

    def __init__(self, responses: list[Any]) -> None:
        super().__init__(responses)
        object.__setattr__(self, "started", threading.Event())

    async def _block_first_call(self) -> None:
        if not self.started.is_set():
            self.started.set()
            while True:
                await asyncio.sleep(0.01)

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        await self._block_first_call()
        return await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[no-untyped-def]
        await self._block_first_call()
        async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk


def _call_tools(*descriptions: str) -> Any:
    """One model response calling the tools whose descriptions contain ``descriptions``, in order."""

    def response(*, tools: list[dict[str, Any]], **_: Any) -> LLMResult:
        calls = [
            {
                "id": f"call_{index}",
                "type": "function",
                "function": {
                    "name": next(t["function"]["name"] for t in tools if text in t["function"]["description"]),
                    "arguments": "{}",
                },
            }
            for index, text in enumerate(descriptions, start=1)
        ]
        return LLMResult(message={"role": "assistant", "content": "", "tool_calls": calls}, finish_reason="tool_calls")

    return response


def _run_in_thread(target: Any) -> tuple[threading.Thread, dict[str, Any]]:
    result: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: result.setdefault("response", target()))
    thread.start()
    return thread, result


def test_stop_during_model_call_ends_turn_as_stopped_and_resume_answers_it() -> None:
    llm = FirstCallBlocksLLM([assistant("resumed answer")])
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        conversation = client.post("/conversations", json={"title": "Stoppable"}).json()
        thread, result = _run_in_thread(
            lambda: client.post("/chat", json={"message": "long question", "conversation_id": conversation["id"]})
        )
        assert llm.started.wait(timeout=2)
        stop = client.post(f"/conversations/{conversation['id']}/stop", json={})
        thread.join(timeout=5)
        stopped = client.get(f"/conversations/{conversation['id']}").json()
        stopped_trace = client.get(f"/traces/{result['response'].json()['trace_id']}").json()

        resumed = client.post(f"/conversations/{conversation['id']}/resume", json={})
        completed = client.get(f"/conversations/{conversation['id']}").json()

    assert stop.status_code == 202
    assert result["response"].status_code == 200
    assert result["response"].json()["status"] == "stopped"
    assert stopped["run_status"] == "stopped"
    assert stopped["run_error"] is None
    assert [message["role"] for message in stopped["messages"]] == ["user"]
    assert stopped_trace["status"] == "cancelled"

    assert resumed.status_code == 200
    assert resumed.json()["status"] == "completed"
    assert resumed.json()["content"] == "resumed answer"
    assert resumed.json()["trace_id"] != result["response"].json()["trace_id"]
    assert completed["run_status"] == "idle"
    assert [(m["role"], m["content"]) for m in completed["messages"]] == [
        ("user", "long question"),
        ("assistant", "resumed answer"),
    ]
    user_turns = [m for m in llm.calls[-1]["messages"] if m["role"] == "user"]
    assert [m["content"] for m in user_turns] == ["long question"]


def _sse_events(body: str) -> list[dict[str, Any]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


def test_stopped_stream_completes_as_stopped_and_resume_streams_the_answer() -> None:
    llm = FirstCallBlocksLLM([assistant("streamed after resume")])
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        conversation = client.post("/conversations", json={"title": "Stream"}).json()
        thread, result = _run_in_thread(
            lambda: client.post(
                "/chat/stream", json={"message": "stream question", "conversation_id": conversation["id"]}
            )
        )
        assert llm.started.wait(timeout=2)
        client.post(f"/conversations/{conversation['id']}/stop", json={})
        thread.join(timeout=5)
        resumed = client.post(f"/conversations/{conversation['id']}/resume/stream", json={})

    stopped_events = _sse_events(result["response"].text)
    assert [event["type"] for event in stopped_events] == ["message.completed"]
    assert stopped_events[0]["response"]["status"] == "stopped"
    resumed_events = _sse_events(resumed.text)
    assert "".join(e["delta"] for e in resumed_events if e["type"] == "message.delta") == "streamed after resume"
    assert resumed_events[-1]["response"]["status"] == "completed"


def _stop_during_tool_batch(client: TestClient, slow_started: threading.Event) -> dict[str, Any]:
    for tool_id, description in (("demo.fast", "Fast lookup"), ("demo.slow", "Slow operation")):
        registered = client.post(
            "/tools",
            json={
                "tool_id": tool_id,
                "name": description,
                "description": description,
                "input_schema": {"type": "object", "properties": {}},
                "endpoint": f"https://tool.invalid/{tool_id}",
            },
        )
        assert registered.status_code == 201
    conversation = client.post("/conversations", json={"title": "Tools"}).json()
    thread, result = _run_in_thread(
        lambda: client.post("/chat", json={"message": "use both tools", "conversation_id": conversation["id"]})
    )
    assert slow_started.wait(timeout=5)
    assert client.post(f"/conversations/{conversation['id']}/stop", json={}).status_code == 202
    thread.join(timeout=5)
    assert result["response"].json()["status"] == "stopped"
    return conversation


def _tool_backend() -> tuple[httpx.AsyncClient, threading.Event, list[str]]:
    slow_started = threading.Event()
    executed: list[str] = []

    async def remote(request: httpx.Request) -> httpx.Response:
        executed.append(request.url.path)
        if request.url.path.endswith("demo.slow"):
            slow_started.set()
            while True:
                await asyncio.sleep(0.01)
        return httpx.Response(200, json={"result": "fast result"})

    return httpx.AsyncClient(transport=httpx.MockTransport(remote)), slow_started, executed


def _tool_results(messages: list[dict[str, Any]]) -> dict[str, str]:
    return {m["tool_call_id"]: m["content"] for m in messages if m["role"] == "tool"}


def test_resume_after_stop_during_tool_batch_keeps_finished_results_and_never_reruns_unfinished_calls() -> None:
    http_client, slow_started, executed = _tool_backend()
    llm = ScriptedLLM([_call_tools("Fast lookup", "Slow operation"), assistant("partial answer")])
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=http_client)) as client:
        conversation = _stop_during_tool_batch(client, slow_started)
        stopped = client.get(f"/conversations/{conversation['id']}").json()
        resumed = client.post(f"/conversations/{conversation['id']}/resume", json={})

    assert executed == ["/demo.fast", "/demo.slow"]
    results = _tool_results(stopped["messages"])
    assert "fast result" in results["call_1"]
    assert results["call_2"] == STOPPED_TOOL_RESULT
    assert resumed.json()["content"] == "partial answer"
    assert _tool_results(llm.calls[-1]["messages"]) == results


def test_new_turn_after_stop_during_tool_batch_keeps_finished_results() -> None:
    http_client, slow_started, executed = _tool_backend()
    llm = ScriptedLLM([_call_tools("Fast lookup", "Slow operation"), assistant("next answer")])
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=http_client)) as client:
        conversation = _stop_during_tool_batch(client, slow_started)
        response = client.post("/chat", json={"message": "never mind", "conversation_id": conversation["id"]})
        completed = client.get(f"/conversations/{conversation['id']}").json()

    assert executed == ["/demo.fast", "/demo.slow"]
    assert response.json()["content"] == "next answer"
    request = llm.calls[-1]["messages"]
    assert "fast result" in _tool_results(request)["call_1"]
    assert _tool_results(request)["call_2"] == STOPPED_TOOL_RESULT
    assert [m["tool_call_id"] for m in completed["messages"] if m["role"] == "tool"] == ["call_1", "call_2"]
    assert [m["role"] for m in completed["messages"]] == ["user", "assistant", "tool", "tool", "user", "assistant"]


def test_stop_during_approved_call_closes_it_without_rerunning_it_on_resume() -> None:
    http_client, slow_started, executed = _tool_backend()
    llm = ScriptedLLM([_call_tools("Slow operation"), assistant("answer after approval stop")])
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=http_client)) as client:
        client.post(
            "/tools",
            json={
                "tool_id": "demo.slow",
                "name": "Slow operation",
                "description": "Slow operation",
                "input_schema": {"type": "object", "properties": {}},
                "endpoint": "https://tool.invalid/demo.slow",
                "side_effect_level": "high",
            },
        )
        pending = client.post("/chat", json={"message": "run it"}).json()
        conversation_id = pending["conversation_id"]
        thread, result = _run_in_thread(lambda: client.post(f"/approvals/{pending['approval']['id']}/confirm", json={}))
        assert slow_started.wait(timeout=5)
        client.post(f"/conversations/{conversation_id}/stop", json={})
        thread.join(timeout=5)
        stopped = client.get(f"/conversations/{conversation_id}").json()
        resumed = client.post(f"/conversations/{conversation_id}/resume", json={})

    assert result["response"].json()["status"] == "stopped"
    assert stopped["run_status"] == "stopped"
    assert _tool_results(stopped["messages"]) == {"call_1": STOPPED_TOOL_RESULT}
    assert resumed.json()["content"] == "answer after approval stop"
    assert executed == ["/demo.slow"]


class SlowMemoryRepository(InMemoryRepository):
    """Holds a turn in its planning phase, before the agent starts."""

    def __init__(self) -> None:
        super().__init__()
        self.searching = threading.Event()
        self.release = threading.Event()

    async def search_memories(self, query: str, **kwargs: Any) -> list[MemoryRecord]:
        self.searching.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        return await super().search_memories(query, **kwargs)


def test_stop_before_the_agent_starts_keeps_the_question_for_resume() -> None:
    repository = SlowMemoryRepository()
    llm = ScriptedLLM([assistant("late answer")])
    with TestClient(create_app(settings=_settings(), llm=llm, repository=repository)) as client:
        conversation = client.post("/conversations", json={"title": "Early stop"}).json()
        thread, result = _run_in_thread(
            lambda: client.post("/chat", json={"message": "early question", "conversation_id": conversation["id"]})
        )
        assert repository.searching.wait(timeout=2)
        assert client.post(f"/conversations/{conversation['id']}/stop", json={}).status_code == 202
        repository.release.set()
        thread.join(timeout=5)
        resumed = client.post(f"/conversations/{conversation['id']}/resume", json={})

    assert result["response"].json()["status"] == "stopped"
    assert len(llm.calls) == 1
    assert resumed.json()["content"] == "late answer"
    assert [m["content"] for m in llm.calls[0]["messages"] if m["role"] == "user"] == ["early question"]


def test_stop_and_resume_require_a_running_or_stopped_turn() -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([assistant("done")]))) as client:
        conversation = client.post("/conversations", json={"title": "Idle"}).json()
        stop = client.post(f"/conversations/{conversation['id']}/stop", json={})
        client.post("/chat", json={"message": "hello", "conversation_id": conversation["id"]})
        resume = client.post(f"/conversations/{conversation['id']}/resume", json={})

    assert stop.status_code == 409
    assert resume.status_code == 409
