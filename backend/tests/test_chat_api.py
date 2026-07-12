from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import httpx
from fastapi.testclient import TestClient

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.core.llm import EventSink, LLMResult
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool


def _settings(*, max_iterations: int = 8) -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        max_agent_iterations=max_iterations,
    )


def test_chat_preserves_multi_turn_context() -> None:
    llm = ScriptedLLM([assistant("first answer"), assistant("second answer")])
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        first = client.post("/chat", json={"message": "first question"})
        second = client.post(
            "/chat",
            json={
                "message": "follow up",
                "conversation_id": first.json()["conversation_id"],
            },
        )
        conversation = client.get(f"/conversations/{first.json()['conversation_id']}")

    assert first.status_code == 200
    assert first.json()["content"] == "first answer"
    assert second.status_code == 200
    assert second.json()["content"] == "second answer"
    assert [item["role"] for item in conversation.json()["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert any(message.get("content") == "first answer" for message in llm.calls[1]["messages"])


def test_stream_chat_returns_sse_deltas_and_completion() -> None:
    llm = ScriptedLLM([assistant("streamed answer")])
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        with client.stream("POST", "/chat/stream", json={"message": "stream this"}) as response:
            body = "".join(response.iter_text())

    assert response.status_code == 200
    assert "event: message.delta" in body
    assert "streamed answer" in body
    assert "event: message.completed" in body


def test_conversations_can_be_categorized_filtered_and_deleted() -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        created = client.post("/conversations", json={"title": "Roadmap"}).json()
        categorized = client.patch(f"/conversations/{created['id']}", json={"category": "work"})
        work = client.get("/conversations", params={"category": "work"})
        deleted = client.delete(f"/conversations/{created['id']}")
        remaining = client.get("/conversations")

    assert categorized.status_code == 200
    assert categorized.json()["category"] == "work"
    assert [item["id"] for item in work.json()] == [created["id"]]
    assert deleted.status_code == 204
    assert remaining.json() == []


class BlockingLLM:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None = None,
        event_sink: EventSink | None = None,
    ) -> LLMResult:
        del messages, tools, tool_choice
        self.started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.01)
        if event_sink is not None:
            await event_sink({"type": "message.delta", "delta": "finished"})
        return assistant("finished")


def test_conversation_exposes_running_state_until_background_work_finishes() -> None:
    llm = BlockingLLM()
    result: dict[str, Any] = {}
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        conversation = client.post("/conversations", json={"title": "Recoverable"}).json()

        def run_chat() -> None:
            result["response"] = client.post(
                "/chat",
                json={"message": "wait for release", "conversation_id": conversation["id"]},
            )

        thread = threading.Thread(target=run_chat)
        thread.start()
        assert llm.started.wait(timeout=2)
        running = client.get(f"/conversations/{conversation['id']}").json()
        llm.release.set()
        thread.join(timeout=3)
        completed = client.get(f"/conversations/{conversation['id']}").json()

    assert running["run_status"] == "running"
    assert running["active_trace_id"].startswith("trace_")
    assert result["response"].status_code == 200
    assert completed["run_status"] == "idle"
    assert completed["active_trace_id"] is None
    assert completed["messages"][-1]["content"] == "finished"


def test_tool_loop_executes_remote_tool_and_records_trace() -> None:
    captured: list[dict[str, Any]] = []

    async def remote(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={"result": 42})

    llm = ScriptedLLM(
        [
            call_first_tool(arguments='{"a": 17, "b": 25}'),
            assistant("The result is 42."),
        ]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    app = create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)
    with TestClient(app) as client:
        registered = client.post(
            "/tools",
            json={
                "tool_id": "demo.add",
                "name": "Add numbers",
                "description": "Add two integer values.",
                "input_schema": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"],
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {"result": {"type": "integer"}},
                    "required": ["result"],
                },
                "endpoint": "https://tool.invalid/add",
            },
        )
        response = client.post(
            "/chat",
            json={"message": "What is 17 + 25?", "capability": "tool:demo.add"},
        )
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    assert registered.status_code == 201
    assert response.status_code == 200
    assert response.json()["content"] == "The result is 42."
    assert response.json()["iterations"] == 2
    assert captured[0]["arguments"] == {"a": 17, "b": 25}
    assert captured[0]["trace_id"] == response.json()["trace_id"]
    assert any(event["kind"] == "tool.completed" for event in trace.json()["events"])
    assert llm.calls[0]["tool_choice"]["function"]["name"].startswith("tool_")


def test_tool_failure_is_isolated_and_returned_to_the_model() -> None:
    async def remote(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "offline"})

    llm = ScriptedLLM(
        [
            call_first_tool(arguments='{"value": "x"}'),
            assistant("The external tool is temporarily unavailable."),
        ]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post(
            "/tools",
            json={
                "tool_id": "demo.failure",
                "name": "Failure demo",
                "description": "A test dependency.",
                "input_schema": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                },
                "endpoint": "https://tool.invalid/fail",
                "retries": 0,
            },
        )
        response = client.post("/chat", json={"message": "Use the tool"})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    tool_result = next(item for item in llm.calls[1]["messages"] if item["role"] == "tool")
    assert json.loads(tool_result["content"])["error"]["code"] == "DEPENDENCY_FAILED"


def test_high_risk_tool_waits_for_confirmation() -> None:
    calls = 0

    async def remote(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"sent": True})

    llm = ScriptedLLM(
        [
            call_first_tool(arguments='{"recipient": "user@example.com"}'),
            assistant("The message was sent after confirmation."),
        ]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post(
            "/tools",
            json={
                "tool_id": "demo.send",
                "name": "Send message",
                "description": "Send a message to an external recipient.",
                "input_schema": {
                    "type": "object",
                    "properties": {"recipient": {"type": "string"}},
                    "required": ["recipient"],
                },
                "endpoint": "https://tool.invalid/send",
                "side_effect_level": "high",
            },
        )
        pending = client.post("/chat", json={"message": "Send the message"})
        approval_id = pending.json()["approval"]["id"]
        approvals = client.get(
            "/approvals",
            params={"conversation_id": pending.json()["conversation_id"], "status": "pending"},
        )
        assert calls == 0
        confirmed = client.post(f"/approvals/{approval_id}/confirm", json={})

    assert pending.status_code == 200
    assert pending.json()["status"] == "approval_required"
    assert [item["id"] for item in approvals.json()] == [approval_id]
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "completed"
    assert calls == 1


def test_iteration_limit_stops_repeated_tool_loop() -> None:
    async def remote(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True})

    llm = ScriptedLLM(
        [
            call_first_tool(call_id="call_1"),
            call_first_tool(call_id="call_2"),
        ]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(
        create_app(settings=_settings(max_iterations=2), llm=llm, capability_http_client=capability_client)
    ) as client:
        client.post(
            "/tools",
            json={
                "tool_id": "demo.loop",
                "name": "Loop demo",
                "description": "A test capability.",
                "input_schema": {"type": "object"},
                "endpoint": "https://tool.invalid/loop",
            },
        )
        response = client.post("/chat", json={"message": "Loop"})

    assert response.status_code == 200
    assert response.json()["status"] == "failed"
    assert response.json()["iterations"] == 2
    assert "stopped after 2" in response.json()["content"]


def test_validation_errors_use_standard_error_protocol() -> None:
    llm = ScriptedLLM([])
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        response = client.post("/chat", json={"message": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert response.json()["error"]["trace_id"].startswith("request_")
