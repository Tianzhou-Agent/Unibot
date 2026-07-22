from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from typing import Any

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.chat import TraceRecord
from tianzhou_agent_platform.core.conversation import ConversationCreate
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.core.llm import EventSink, LLMResult
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool


def _settings(*, max_iterations: int = 8) -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model="test-model",
        max_agent_iterations=max_iterations,
    )


def test_chat_preserves_multi_turn_context() -> None:
    llm = ScriptedLLM(
        [
            assistant("NO_AINA_MATCH"),
            assistant("first answer"),
            assistant("NO_AINA_MATCH"),
            assistant("second answer"),
        ]
    )
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
    assert any(message.get("content") == "first answer" for message in llm.calls[2]["messages"])


def test_chat_uses_ui_context_without_persisting_it() -> None:
    llm = ScriptedLLM([assistant("NO_AINA_MATCH"), assistant("done")])
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        response = client.post(
            "/chat",
            json={"message": "继续修改", "ui_context": "任务 ID：task-1\n章节 ID：section-1"},
        )
        conversation = client.get(f"/conversations/{response.json()['conversation_id']}").json()

    routed_user_message = llm.calls[0]["messages"][-1]["content"]
    assert "<ui_context>" in routed_user_message
    assert "任务 ID：task-1" in routed_user_message
    assert conversation["messages"][0]["content"] == "继续修改"


def test_stream_chat_returns_sse_deltas_and_completion() -> None:
    llm = ScriptedLLM([assistant("NO_AINA_MATCH"), assistant("streamed answer")])
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


def test_get_conversation_recovers_running_state_when_active_trace_is_missing() -> None:
    repository = InMemoryRepository()

    async def seed_interrupted_run() -> str:
        conversation = await repository.create_conversation(ConversationCreate(title="Interrupted"))
        await repository.start_conversation_run(conversation.id, "trace_missing")
        return conversation.id

    conversation_id = asyncio.run(seed_interrupted_run())
    with TestClient(create_app(settings=_settings(), repository=repository, llm=ScriptedLLM([]))) as client:
        response = client.get(f"/conversations/{conversation_id}")

    assert response.status_code == 200
    assert response.json()["run_status"] == "failed"
    assert response.json()["active_trace_id"] is None
    assert response.json()["run_error"] == "上一次处理未正常结束，请重新发送请求。"


class TraceCreationFailureRepository(InMemoryRepository):
    async def create_trace(self, trace: TraceRecord) -> TraceRecord:
        del trace
        raise PlatformError(
            "DEPENDENCY_FAILED",
            "Trace storage is unavailable",
            status_code=503,
            source="storage",
        )


def test_trace_creation_failure_does_not_leave_conversation_running() -> None:
    repository = TraceCreationFailureRepository()
    conversation = asyncio.run(repository.create_conversation(ConversationCreate(title="Trace failure")))

    with TestClient(create_app(settings=_settings(), repository=repository, llm=ScriptedLLM([]))) as client:
        response = client.post(
            "/chat",
            json={"message": "Trigger trace failure", "conversation_id": conversation.id},
        )
        recovered = client.get(f"/conversations/{conversation.id}")

    assert response.status_code == 503
    assert response.json()["error"]["message"] == "Trace storage is unavailable"
    assert recovered.json()["run_status"] == "idle"
    assert recovered.json()["active_trace_id"] is None


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
        return httpx.Response(200, json={"result": 42, "authorization": "Bearer remote-secret-value"})

    llm = ScriptedLLM(
        [
            call_first_tool(arguments='{"a": 17, "b": 25, "api_key": "tool-secret-value"}'),
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
                    "properties": {
                        "a": {"type": "integer"},
                        "b": {"type": "integer"},
                        "api_key": {"type": "string"},
                    },
                    "required": ["a", "b", "api_key"],
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "properties": {
                        "result": {"type": "integer"},
                        "authorization": {"type": "string"},
                    },
                    "required": ["result"],
                },
                "endpoint": "https://tool.invalid/add",
            },
        )
        response = client.post(
            "/chat",
            json={
                "message": "What is 17 + 25? password=customer-secret-value",
                "capability": "tool:demo.add",
            },
        )
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    assert registered.status_code == 201
    assert response.status_code == 200
    assert response.json()["content"] == "The result is 42."
    assert response.json()["iterations"] == 2
    assert captured[0]["arguments"] == {"a": 17, "b": 25, "api_key": "tool-secret-value"}
    assert captured[0]["trace_id"] == response.json()["trace_id"]
    events = trace.json()["events"]
    request_event = next(event for event in events if event["kind"] == "user.request")
    assert request_event["details"]["message_id"].startswith("msg_")
    assert request_event["details"]["content"] == "What is 17 + 25? password=[REDACTED]"
    assert request_event["details"]["content_sha256"] == hashlib.sha256(
        b"What is 17 + 25? password=customer-secret-value"
    ).hexdigest()
    discovery = next(event for event in events if event["kind"] == "capability.discovery")["details"]
    assert discovery["aina_graph"]["available_count"] == 3
    assert {item["id"] for item in discovery["aina_graph"]["available"]} == {
        "unibot-assistant",
        "unibot-memory",
        "unibot-scheduler",
    }
    assert discovery["aina_graph"]["counts"] == {"builtin_aina": 3, "remote_aina": 0}
    assert discovery["model_scope"]["counts"] == {
        "remote_tool": 1,
        "remote_aina": 0,
        "builtin_capability": 0,
    }
    assert discovery["model_scope"]["by_aina"] == []
    assert discovery["model_scope"]["standalone"] == [
        {
            "id": "demo.add",
            "kind": "tool",
            "function_name": llm.calls[0]["tool_choice"]["function"]["name"],
            "display_name": "Add numbers",
            "requires_confirmation": False,
            "owner_aina_id": None,
        }
    ]
    requested_event = next(event for event in events if event["kind"] == "tool.requested")
    assert requested_event["details"]["call_id"] == "call_1"
    assert requested_event["details"]["arguments"] == {"a": 17, "b": 25, "api_key": "[REDACTED]"}
    completed_event = next(event for event in events if event["kind"] == "tool.completed")
    assert completed_event["details"]["result"] == {"result": 42, "authorization": "[REDACTED]"}
    assert completed_event["details"]["result_size_bytes"] > 0
    final_event = next(event for event in events if event["kind"] == "final.response")
    assert final_event["details"]["content"] == "The result is 42."
    assert final_event["details"]["message_id"] == response.json()["message_id"]
    serialized_trace = json.dumps(trace.json())
    assert "customer-secret-value" not in serialized_trace
    assert "tool-secret-value" not in serialized_trace
    assert "remote-secret-value" not in serialized_trace
    assert llm.calls[0]["tool_choice"]["function"]["name"].startswith("tool_")


def test_tool_failure_is_isolated_and_returned_to_the_model() -> None:
    async def remote(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "offline"})

    llm = ScriptedLLM(
        [
            assistant("NO_AINA_MATCH"),
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
    tool_result = next(item for item in llm.calls[2]["messages"] if item["role"] == "tool")
    assert json.loads(tool_result["content"])["error"]["code"] == "DEPENDENCY_FAILED"


def test_high_risk_tool_waits_for_confirmation() -> None:
    calls = 0

    async def remote(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"sent": True})

    llm = ScriptedLLM(
        [
            assistant("NO_AINA_MATCH"),
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


def test_high_risk_tool_denial_closes_pending_call_without_execution() -> None:
    calls = 0

    async def remote(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"sent": True})

    llm = ScriptedLLM([call_first_tool(arguments='{"recipient":"user@example.com"}')])
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post(
            "/tools",
            json={
                "tool_id": "demo.denied-send",
                "name": "Denied send",
                "description": "Send a message only after approval.",
                "input_schema": {
                    "type": "object",
                    "properties": {"recipient": {"type": "string"}},
                    "required": ["recipient"],
                },
                "endpoint": "https://tool.invalid/send",
                "side_effect_level": "high",
            },
        )
        pending = client.post(
            "/chat",
            json={"message": "Send the message", "capability": "tool:demo.denied-send"},
        ).json()
        denied = client.post(f"/approvals/{pending['approval']['id']}/deny", json={})
        conversation = client.get(f"/conversations/{pending['conversation_id']}")
        trace = client.get(f"/traces/{pending['trace_id']}")

    assert denied.status_code == 200
    assert denied.json()["status"] == "denied"
    assert calls == 0
    assert conversation.json()["run_status"] == "idle"
    assert conversation.json()["messages"][-1]["content"] == "The requested operation was cancelled."
    assert any(event["kind"] == "approval.denied" for event in trace.json()["events"])


def test_new_turn_cancels_pending_approval_and_closes_trace() -> None:
    calls = 0

    async def remote(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"sent": True})

    llm = ScriptedLLM(
        [
            call_first_tool(arguments='{"recipient": "user@example.com"}'),
            assistant("NO_AINA_MATCH"),
            assistant("Understood, skipping that action."),
        ]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post(
            "/tools",
            json={
                "tool_id": "demo.abandoned-send",
                "name": "Abandoned send",
                "description": "Approval is abandoned by a new turn.",
                "input_schema": {
                    "type": "object",
                    "properties": {"recipient": {"type": "string"}},
                    "required": ["recipient"],
                },
                "endpoint": "https://tool.invalid/send",
                "side_effect_level": "high",
            },
        )
        pending = client.post(
            "/chat",
            json={"message": "Send the message", "capability": "tool:demo.abandoned-send"},
        ).json()
        follow_up = client.post(
            "/chat",
            json={"message": "Never mind", "conversation_id": pending["conversation_id"]},
        )
        approvals = client.get("/approvals", params={"conversation_id": pending["conversation_id"]})
        trace = client.get(f"/traces/{pending['trace_id']}")

    assert follow_up.status_code == 200
    assert calls == 0
    assert [item["status"] for item in approvals.json()] == ["denied"]
    assert trace.json()["status"] == "completed"
    assert any(event["kind"] == "approval.cancelled" for event in trace.json()["events"])


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
