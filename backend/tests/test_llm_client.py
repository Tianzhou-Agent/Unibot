from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.llm import OpenAICompatibleClient


@pytest.mark.asyncio
async def test_named_tool_choice_falls_back_for_incompatible_provider() -> None:
    requests: list[dict[str, Any]] = []

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
    client = OpenAICompatibleClient(settings, http_client)
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
