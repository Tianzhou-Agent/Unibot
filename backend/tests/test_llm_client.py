from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.chat import LLMCallRecord
from tianzhou_agent_platform.core.llm import OpenAICompatibleClient, _response_from_result, _result_from_message


@pytest.mark.asyncio
async def test_named_tool_choice_falls_back_for_incompatible_provider() -> None:
    requests: list[dict[str, Any]] = []
    recorded_calls: dict[str, LLMCallRecord] = {}

    async def record_call(call: LLMCallRecord) -> None:
        recorded_calls[call.call_id] = call

    async def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            return httpx.Response(
                400,
                json={"error": {"message": "Thinking mode does not support this tool_choice"}},
            )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "demo_add", "arguments": '{"a":17,"b":25}'},
                                }
                            ],
                        },
                    }
                ]
            },
        )

    settings = AgentSettings(
        _env_file=None,
        llm_base_url="https://provider.invalid/v1",
        llm_api_key="test-key",
        llm_model="thinking-model",
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    client = OpenAICompatibleClient(settings, http_client, call_sink=record_call)
    result = await client.complete(
        messages=[
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Add 17 and 25."},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "demo_add",
                    "description": "Add numbers.",
                    "parameters": {"type": "object"},
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": "demo_add"}},
    )
    await http_client.aclose()

    assert len(requests) == 2
    assert requests[0]["tool_choice"]["function"]["name"] == "demo_add"
    assert "tool_choice" not in requests[1]
    assert "explicitly selected function demo_add" in requests[1]["messages"][0]["content"]
    assert result.message["tool_calls"][0]["function"]["name"] == "demo_add"
    assert [call.status for call in recorded_calls.values()] == ["failed", "completed"]
    failed, completed = recorded_calls.values()
    assert failed.endpoint == "https://provider.invalid/v1/chat/completions"
    assert failed.response == {
        "status_code": 400,
        "body": {"error": {"message": "Thinking mode does not support this tool_choice"}},
    }
    assert completed.request["messages"][0]["content"].endswith(
        "Call that function before answering the user."
    )
    assert completed.request["context_window"] == settings.context_window_tokens
    assert completed.request["estimated_prompt_tokens"] > 0
    assert completed.response is not None
    assert completed.response["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "demo_add"


@pytest.mark.asyncio
async def test_named_tool_choice_uses_non_streaming_request_with_event_sink() -> None:
    requests: list[dict[str, Any]] = []

    async def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append(payload)
        if payload["stream"]:
            return httpx.Response(400, json={"error": {"message": "tool_choice is unsupported with stream"}})
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "tool_calls",
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_list",
                                    "type": "function",
                                    "function": {"name": "list_app", "arguments": "{}"},
                                }
                            ],
                        },
                    }
                ]
            },
        )

    settings = AgentSettings(
        _env_file=None,
        llm_base_url="https://provider.invalid/v1",
        llm_api_key="test-key",
        llm_model="stream-incompatible-model",
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    client = OpenAICompatibleClient(settings, http_client)
    events: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    result = await client.complete(
        messages=[{"role": "user", "content": "List applications"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "list_app",
                    "description": "List applications.",
                    "parameters": {"type": "object"},
                },
            }
        ],
        tool_choice={"type": "function", "function": {"name": "list_app"}},
        event_sink=sink,
    )
    await http_client.aclose()

    assert len(requests) == 1
    assert requests[0]["stream"] is False
    assert result.message["tool_calls"][0]["function"]["name"] == "list_app"
    assert events == []


@pytest.mark.asyncio
async def test_langchain_streaming_emits_message_deltas(caplog: pytest.LogCaptureFixture) -> None:
    recorded_calls: dict[str, LLMCallRecord] = {}

    async def record_call(call: LLMCallRecord) -> None:
        recorded_calls[call.call_id] = call

    async def provider(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        assert payload["stream"] is True
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            text=(
                'data: {"id":"chat-1","object":"chat.completion.chunk","created":1,'
                '"model":"test-model","choices":[{"index":0,"delta":{"content":"Hello"},'
                '"finish_reason":null}]}\n\n'
                'data: {"id":"chat-1","object":"chat.completion.chunk","created":1,'
                '"model":"test-model","choices":[{"index":0,"delta":{"content":" world"},'
                '"finish_reason":"stop"}]}\n\n'
                "data: [DONE]\n\n"
            ),
        )

    settings = AgentSettings(
        _env_file=None,
        llm_base_url="https://provider.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
    )
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    client = OpenAICompatibleClient(settings, http_client, call_sink=record_call)
    events: list[dict[str, Any]] = []

    async def sink(event: dict[str, Any]) -> None:
        events.append(event)

    result = await client.complete(
        messages=[{"role": "user", "content": "Say hello"}],
        tools=[],
        event_sink=sink,
    )
    await http_client.aclose()

    assert result.message["content"] == "Hello world"
    assert result.usage_estimated is True
    assert result.input_tokens > 0
    assert result.output_tokens > 0
    assert events == [
        {"type": "message.delta", "delta": "Hello"},
        {"type": "message.delta", "delta": " world"},
    ]
    recorded = next(iter(recorded_calls.values()))
    assert recorded.status == "completed"
    assert recorded.request["stream"] is True
    assert recorded.first_token_at is not None
    assert recorded.ttft_ms is not None
    assert recorded.ttft_ms >= 0
    assert result.first_token_at == recorded.first_token_at
    assert result.ttft_ms == recorded.ttft_ms
    assert recorded.response is not None
    assert recorded.response["choices"][0]["message"]["content"] == "Hello world"
    assert recorded.response["usage"]["estimated"] is True
    assert recorded.response["usage"]["source"] == "estimated"
    assert "completed without usage metadata" in caplog.text
    result.message["widgets"] = [{"id": "document-outline", "kind": "document_outline"}]
    assert "widgets" not in recorded.response["choices"][0]["message"]


def test_reported_usage_remains_exact() -> None:
    result = _result_from_message(
        AIMessage(
            content="Hello",
            usage_metadata={"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
            response_metadata={"finish_reason": "stop"},
        )
    )

    response = _response_from_result("test-model", result)

    assert result.usage_estimated is False
    assert response["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 3,
        "total_tokens": 15,
    }
