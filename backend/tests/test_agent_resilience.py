from __future__ import annotations

import json
from typing import cast

import httpx
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.llm import LLMResult
from tianzhou_agent_platform.main import create_app


def _settings() -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key=SecretStr("test-key"),
        llm_model="test-model",
        max_agent_iterations=6,
    )


def _tool_definition(**overrides: object) -> dict[str, object]:
    definition: dict[str, object] = {
        "tool_id": "resilience.tool",
        "name": "Resilience tool",
        "description": "Exercise remote capability recovery behavior.",
        "input_schema": {
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"result": {"type": "integer"}},
            "required": ["result"],
        },
        "endpoint": "https://tool.invalid/invoke",
        "retries": 0,
    }
    definition.update(overrides)
    return definition


def _tool_error(llm: ScriptedLLM) -> dict[str, object]:
    tool_message = next(item for item in llm.calls[1]["messages"] if item["role"] == "tool")
    return cast(dict[str, object], json.loads(tool_message["content"])["error"])


def test_invalid_json_arguments_are_returned_to_model_without_remote_call() -> None:
    calls = 0

    async def remote(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"result": 1})

    llm = ScriptedLLM([call_first_tool(arguments="{invalid"), assistant("The arguments were invalid.")])
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post("/tools", json=_tool_definition())
        response = client.post(
            "/chat",
            json={"message": "Run it", "capability": "tool:resilience.tool"},
        )

    assert response.json()["status"] == "completed"
    assert calls == 0
    assert _tool_error(llm)["code"] == "INVALID_REQUEST"


def test_input_schema_failure_is_returned_to_model_without_remote_call() -> None:
    calls = 0

    async def remote(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"result": 1})

    llm = ScriptedLLM([call_first_tool(arguments='{"value":"wrong"}'), assistant("Use an integer.")])
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post("/tools", json=_tool_definition())
        response = client.post(
            "/chat",
            json={"message": "Run it", "capability": "tool:resilience.tool"},
        )

    assert response.json()["status"] == "completed"
    assert calls == 0
    assert _tool_error(llm)["code"] == "INVALID_REQUEST"


def test_output_schema_failure_is_isolated_from_agent_loop() -> None:
    async def remote(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": "wrong"})

    llm = ScriptedLLM([call_first_tool(arguments='{"value":1}'), assistant("The tool returned invalid data.")])
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post("/tools", json=_tool_definition())
        response = client.post(
            "/chat",
            json={"message": "Run it", "capability": "tool:resilience.tool"},
        )
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    assert response.json()["status"] == "completed"
    assert _tool_error(llm)["code"] == "INVALID_REQUEST"
    assert any(
        event["kind"] == "tool.failed" and event["details"]["code"] == "INVALID_REQUEST"
        for event in trace.json()["events"]
    )


def test_timeout_retries_then_returns_retryable_error_to_model() -> None:
    calls = 0

    async def remote(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("slow dependency", request=request)

    llm = ScriptedLLM([call_first_tool(arguments='{"value":1}'), assistant("The tool timed out.")])
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post("/tools", json=_tool_definition(retries=2))
        response = client.post(
            "/chat",
            json={"message": "Run it", "capability": "tool:resilience.tool"},
        )

    assert response.json()["status"] == "completed"
    assert calls == 3
    assert _tool_error(llm) == {
        "code": "TIMEOUT",
        "message": "Remote tool timed out",
        "retryable": True,
    }


def test_repeated_identical_call_is_blocked_after_one_remote_execution() -> None:
    calls = 0

    async def remote(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"result": 1})

    llm = ScriptedLLM(
        [
            call_first_tool(arguments='{"value":1}', call_id="call_1"),
            call_first_tool(arguments='{ "value": 1 }', call_id="call_2"),
            assistant("I stopped repeating the tool call."),
        ]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post("/tools", json=_tool_definition())
        response = client.post(
            "/chat",
            json={"message": "Run it", "capability": "tool:resilience.tool"},
        )

    assert response.json()["status"] == "completed"
    assert calls == 1
    final_tool_message = next(item for item in llm.calls[2]["messages"] if item.get("tool_call_id") == "call_2")
    assert json.loads(final_tool_message["content"])["error"]["code"] == "CONFLICT"


def test_invalid_aina_protocol_response_is_returned_to_model() -> None:
    async def remote(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/describe"):
            return httpx.Response(200, json={"protocol_version": "1.0"})
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "healthy"})
        return httpx.Response(200, json={"status": "completed", "outputs": []})

    llm = ScriptedLLM(
        [
            call_first_tool(prefix="aina_", arguments='{"input":"run"}'),
            assistant("The AINA returned an invalid protocol response."),
        ]
    )
    manifest = {
        "protocol_version": "1.0",
        "aina": {
            "id": "com.example.invalid-response",
            "name": "Invalid response AINA",
            "version": "1.0.0",
            "description": "Returns an invalid invocation response for recovery testing.",
            "publisher": {"id": "tests", "name": "Tests"},
        },
        "runtime": {
            "type": "remote",
            "endpoint": "https://aina.invalid/runtime",
            "streaming": False,
            "async_tasks": False,
        },
        "capabilities": {"skills": [], "tools": [], "ui": [], "events": []},
        "permissions": [],
        "authentication": {"type": "none"},
    }
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post("/ainas", json=manifest)
        client.post("/ainas/com.example.invalid-response/install", json={})
        response = client.post(
            "/chat",
            json={
                "message": "Run the invalid response AINA",
                "capability": "aina:com.example.invalid-response",
            },
        )

    assert response.json()["status"] == "completed"
    assert _tool_error(llm)["code"] == "DEPENDENCY_FAILED"


def test_empty_model_response_marks_run_failed() -> None:
    llm = ScriptedLLM(
        [
            assistant("NO_AINA_MATCH"),
            LLMResult(
                message={"role": "assistant", "content": ""},
                finish_reason="stop",
            )
        ]
    )
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        response = client.post("/chat", json={"message": "Answer me"})
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    assert response.json()["status"] == "failed"
    assert response.json()["content"] == "The model returned an empty response."
    assert trace.json()["status"] == "failed"
