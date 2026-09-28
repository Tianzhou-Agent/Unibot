"""Native provider behavior of the agent: requests the app's ChatOpenAI models send to an OpenAI-compatible API."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app


def _completion(message: dict[str, Any], *, finish_reason: str = "stop") -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "created": 0,
            "model": "team-model",
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        },
    )


def _stream(*deltas: str) -> httpx.Response:
    chunks = [
        {
            "id": "chat-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "team-model",
            "choices": [{"index": 0, "delta": {"content": delta}, "finish_reason": None}],
        }
        for delta in deltas
    ]
    chunks.append(
        {
            "id": "chat-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "team-model",
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
        }
    )
    body = "".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, text=body)


@contextmanager
def _client(
    monkeypatch: Any,
    provider: Callable[[httpx.Request], httpx.Response],
    *,
    context_window_tokens: int = 64_000,
) -> Iterator[TestClient]:
    """App whose actor selected a provider model served by ``provider``."""
    import tianzhou_agent_platform.main as main_module
    from tianzhou_agent_platform.model_providers.factory import create_model_from_runtime

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    monkeypatch.setattr(
        main_module,
        "create_model_from_runtime",
        lambda runtime, **options: create_model_from_runtime(runtime, http_client=http_client, **options),
    )
    settings = AgentSettings(_env_file=None)  # type: ignore[call-arg]
    with TestClient(create_app(settings=settings)) as client:
        created = client.post(
            "/model-settings/providers",
            json={
                "provider_type": "openai",
                "name": "Team provider",
                "base_url": "https://provider.invalid/v1",
                "api_key": "provider-key",
                "models": [
                    {"name": "Team model", "model": "team-model", "context_window_tokens": context_window_tokens}
                ],
            },
        ).json()
        client.post(
            f"/model-settings/providers/{created['id']}/models/{created['models'][0]['id']}/default",
            json={},
        )
        yield client
    asyncio.run(http_client.aclose())


def test_model_output_reserve_is_sent_with_every_request(monkeypatch: Any) -> None:
    requests: list[dict[str, Any]] = []

    def provider(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        return _completion({"role": "assistant", "content": "ok"})

    with _client(monkeypatch, provider, context_window_tokens=4_096) as client:
        response = client.post("/chat", json={"message": "Hello"})

    assert response.json()["content"] == "ok"
    assert requests[0]["max_completion_tokens"] == 1_024


def test_named_tool_choice_falls_back_for_incompatible_provider(monkeypatch: Any) -> None:
    requests: list[dict[str, Any]] = []

    def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(400, json={"error": {"message": "Thinking mode does not support this tool_choice"}})
        if len(requests) == 2:
            name = payload["tools"][0]["function"]["name"]
            return _completion(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_list", "type": "function", "function": {"name": name, "arguments": "{}"}}
                    ],
                },
                finish_reason="tool_calls",
            )
        return _completion({"role": "assistant", "content": "Here are the applications."})

    with _client(monkeypatch, provider) as client:
        response = client.post("/chat", json={"message": "List applications", "capability": "builtin:list_app"})
        llm_calls = client.get("/llm-calls").json()

    assert response.json()["content"] == "Here are the applications."
    forced = requests[0]["tool_choice"]["function"]["name"]
    assert forced.startswith("builtin_list_app_")
    assert "tool_choice" not in requests[1]
    assert requests[1]["messages"][0]["content"].endswith(
        f"The caller explicitly selected function {forced}. Call that function before answering the user."
    )
    # Both requests of the forced turn are recorded separately.
    statuses = [call["status"] for call in sorted(llm_calls, key=lambda call: call["created_at"])]
    assert statuses == ["failed", "completed", "completed"]


def test_forced_tool_turn_is_not_streamed(monkeypatch: Any) -> None:
    requests: list[dict[str, Any]] = []

    def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if payload.get("tool_choice"):
            if payload.get("stream"):
                return httpx.Response(400, json={"error": {"message": "tool_choice is unsupported with stream"}})
            name = payload["tools"][0]["function"]["name"]
            return _completion(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_list", "type": "function", "function": {"name": name, "arguments": "{}"}}
                    ],
                },
                finish_reason="tool_calls",
            )
        return _stream("Hello", " world")

    with _client(monkeypatch, provider) as client:
        with client.stream(
            "POST", "/chat/stream", json={"message": "List applications", "capability": "builtin:list_app"}
        ) as response:
            events = [json.loads(line[5:]) for line in response.iter_lines() if line.startswith("data:")]

    assert [request.get("stream", False) for request in requests] == [False, True]
    assert [event["delta"] for event in events if event["type"] == "message.delta"] == ["Hello", " world"]
    completed = next(event for event in events if event["type"] == "message.completed")
    assert completed["response"]["content"] == "Hello world"


def test_model_calls_keep_complete_redacted_io(monkeypatch: Any) -> None:
    from tianzhou_agent_platform.observability.service import ObservabilityAspect

    long_text = "x" * 5_000
    recorded: list[Any] = []
    record_llm_call = ObservabilityAspect.record_llm_call

    async def capture(self: Any, call: Any) -> None:
        recorded.append(call)
        await record_llm_call(self, call)

    monkeypatch.setattr(ObservabilityAspect, "record_llm_call", capture)

    def provider(request: httpx.Request) -> httpx.Response:
        return _completion({"role": "assistant", "content": long_text})

    with _client(monkeypatch, provider) as client:
        client.post("/chat", json={"message": f"Bearer secret-token {long_text}"})

    completed = next(call for call in recorded if call.status == "completed")
    request_content = completed.request["messages"][-1]["content"]
    assert request_content.startswith("Bearer [REDACTED] ")
    assert request_content.endswith(long_text)
    assert completed.response["choices"][0]["message"]["content"] == long_text
    assert completed.response["usage"] == {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}


def test_connection_error_records_underlying_cause(monkeypatch: Any) -> None:
    def provider(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("proxy refused", request=request)

    with _client(monkeypatch, provider) as client:
        response = client.post("/chat", json={"message": "Hello"})
        [failed] = client.get("/llm-calls").json()

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "DEPENDENCY_FAILED"
    assert "could not be reached" in response.json()["error"]["message"]
    assert failed["status"] == "failed"
    assert "proxy refused" in (failed["error"] or "")


@pytest.mark.parametrize("finish_reason", ["length"])
def test_truncated_provider_answer_fails_the_run(monkeypatch: Any, finish_reason: str) -> None:
    def provider(request: httpx.Request) -> httpx.Response:
        return _completion({"role": "assistant", "content": "Partial"}, finish_reason=finish_reason)

    with _client(monkeypatch, provider) as client:
        response = client.post("/chat", json={"message": "Hello"}).json()

    assert response["status"] == "failed"
    assert response["content"].startswith("Partial")


def test_malformed_tool_arguments_are_returned_to_the_model(monkeypatch: Any) -> None:
    requests: list[dict[str, Any]] = []

    def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            name = next(tool["function"]["name"] for tool in payload["tools"] if "list_app" in tool["function"]["name"])
            return _completion(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {"id": "call_bad", "type": "function", "function": {"name": name, "arguments": '{"x": '}}
                    ],
                },
                finish_reason="tool_calls",
            )
        return _completion({"role": "assistant", "content": f"answer {len(requests)}"})

    with _client(monkeypatch, provider) as client:
        first = client.post("/chat", json={"message": "List applications"}).json()
        second = client.post("/chat", json={"message": "Again", "conversation_id": first["conversation_id"]}).json()
        conversation = client.get(f"/conversations/{first['conversation_id']}").json()
        trace = client.get(f"/traces/{first['trace_id']}").json()

    assert first["status"] == "completed" and first["content"] == "answer 2"
    # The provider received the malformed call together with its error result, in both later requests.
    for request in requests[1:]:
        call = next(message for message in request["messages"] if message.get("tool_calls"))
        assert call["tool_calls"][0]["function"]["arguments"] == '{"x": '
        result = next(message for message in request["messages"] if message.get("tool_call_id") == "call_bad")
        assert json.loads(result["content"])["error"]["code"] == "INVALID_REQUEST"
    assert second["content"] == "answer 3"
    assert [message["role"] for message in conversation["messages"]] == [
        "user", "assistant", "tool", "assistant", "user", "assistant",
    ]
    assert any(event["kind"] == "builtin.failed" and event["details"]["code"] == "INVALID_REQUEST" for event in trace["events"])
