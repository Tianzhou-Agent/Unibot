from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from fastapi.testclient import TestClient

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.llm import OpenAICompatibleClient
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.main import create_app


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_base_url="https://environment.invalid/v1",
        llm_api_key="environment-key",
        llm_model="environment-model",
    )


def _provider_payload() -> dict[str, Any]:
    return {
        "provider_type": "openai",
        "name": "团队 OpenAI",
        "base_url": "https://user-provider.invalid/v1/",
        "api_key": "user-secret-key-value",
        "timeout_seconds": 45,
        "models": [
            {
                "name": "快速模型",
                "model": "team-fast",
                "enabled": True,
                "context_window_tokens": 64_000,
            },
            {"name": "推理模型", "model": "team-reasoning", "enabled": True},
        ],
    }


def test_model_settings_support_multiple_models_and_mask_secrets() -> None:
    data_repository = InMemoryRepository()
    with TestClient(create_app(settings=_settings(), repository=data_repository)) as client:
        initial = client.get("/model-settings")
        created = client.post("/model-settings/providers", json=_provider_payload())
        provider = created.json()
        selected = client.post(
            f"/model-settings/providers/{provider['id']}/models/{provider['models'][1]['id']}/default",
            json={},
        )
        loaded = client.get("/model-settings")

        update_payload = _provider_payload()
        update_payload["api_key"] = ""
        update_payload["models"] = [
            {
                "id": model["id"],
                "name": model["name"],
                "model": model["model"],
                "enabled": model["enabled"],
                "context_window_tokens": model["context_window_tokens"],
            }
            for model in selected.json()["models"]
        ]
        updated = client.put(f"/model-settings/providers/{provider['id']}", json=update_payload)

    assert initial.status_code == 200
    assert initial.json()["active_model"]["source"] == "environment"
    assert created.status_code == 201
    assert created.json()["api_key_masked"] == "use******alue"
    assert "user-secret-key-value" not in created.text
    assert len(created.json()["models"]) == 2
    assert created.json()["models"][0]["context_window_tokens"] == 64_000
    assert created.json()["models"][1]["context_window_tokens"] == 128_000
    assert selected.status_code == 200
    assert loaded.json()["active_model"] == {
        "source": "user",
        "provider_id": provider["id"],
        "provider_name": "团队 OpenAI",
        "model_id": provider["models"][1]["id"],
        "model_name": "推理模型",
        "model": "team-reasoning",
    }
    assert updated.status_code == 200
    assert updated.json()["has_api_key"] is True


def test_model_provider_can_be_deleted() -> None:
    with TestClient(create_app(settings=_settings())) as client:
        created = client.post("/model-settings/providers", json=_provider_payload()).json()
        selected_model = created["models"][0]
        client.post(
            f"/model-settings/providers/{created['id']}/models/{selected_model['id']}/default",
            json={},
        )

        deleted = client.delete(f"/model-settings/providers/{created['id']}")
        loaded = client.get("/model-settings")

    assert deleted.status_code == 204
    assert loaded.status_code == 200
    assert loaded.json()["providers"] == []
    assert loaded.json()["active_model"]["source"] == "environment"


def test_model_discovery_reads_openai_compatible_models_and_reuses_saved_key() -> None:
    requests: list[httpx.Request] = []

    async def provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"id": "team-fast", "object": "model", "context_length": 131_072},
                    {
                        "id": "team-reasoning",
                        "display_name": "Team Reasoning",
                        "top_provider": {"context_length": "200000"},
                    },
                    {"id": "team-fast"},
                ],
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    with TestClient(create_app(settings=_settings(), model_health_http_client=http_client)) as client:
        created = client.post("/model-settings/providers", json=_provider_payload()).json()
        response = client.post(
            "/model-settings/providers/discover-models",
            json={
                "provider_id": created["id"],
                "base_url": "https://user-provider.invalid/v1/",
                "api_key": "",
                "timeout_seconds": 45,
            },
        )

    asyncio.run(http_client.aclose())
    assert response.status_code == 200
    assert response.json() == {
        "models": [
            {"id": "team-fast", "name": "team-fast", "context_window_tokens": 131_072},
            {"id": "team-reasoning", "name": "Team Reasoning", "context_window_tokens": 200_000},
        ]
    }
    assert len(requests) == 1
    assert requests[0].method == "GET"
    assert requests[0].url == httpx.URL("https://user-provider.invalid/v1/models")
    assert requests[0].headers["Authorization"] == "Bearer user-secret-key-value"


def test_model_discovery_keeps_manual_flow_when_models_endpoint_is_missing() -> None:
    requests: list[httpx.Request] = []

    async def provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(404)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    with TestClient(create_app(settings=_settings(), model_health_http_client=http_client)) as client:
        response = client.post(
            "/model-settings/providers/discover-models",
            json={
                "base_url": "https://user-provider.invalid/v1",
                "api_key": "new-provider-key",
            },
        )

    asyncio.run(http_client.aclose())
    assert response.status_code == 502
    assert response.json()["error"]["user_message"] == "该 Provider 未提供可用的 /models 接口，请继续手动添加模型。"
    assert requests[0].headers["Authorization"] == "Bearer new-provider-key"


def test_agent_uses_the_users_selected_model_configuration() -> None:
    requests: list[httpx.Request] = []

    async def provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "selected model response"},
                    }
                ]
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    llm = OpenAICompatibleClient(_settings(), http_client)
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        created = client.post("/model-settings/providers", json=_provider_payload()).json()
        selected_model = created["models"][1]
        client.post(
            f"/model-settings/providers/{created['id']}/models/{selected_model['id']}/default",
            json={},
        )
        response = client.post("/chat", json={"message": "你好"})
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    asyncio.run(http_client.aclose())
    assert response.status_code == 200
    assert response.json()["content"] == "selected model response"
    assert requests
    assert all(request.url == httpx.URL("https://user-provider.invalid/v1/chat/completions") for request in requests)
    assert all(request.headers["Authorization"] == "Bearer user-secret-key-value" for request in requests)
    assert all(json.loads(request.content)["model"] == "team-reasoning" for request in requests)
    assert all(
        event["target_id"] == "team-reasoning"
        for event in trace["events"]
        if event["kind"].startswith("model.")
    )


def test_model_health_check_reports_latency_and_uses_selected_model() -> None:
    requests: list[httpx.Request] = []

    async def provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chat-health",
                "object": "chat.completion",
                "created": 1,
                "model": "team-fast",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "OK"},
                    }
                ],
            },
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
    with TestClient(create_app(settings=_settings(), model_health_http_client=http_client)) as client:
        created = client.post("/model-settings/providers", json=_provider_payload()).json()
        model = created["models"][0]
        response = client.post(
            f"/model-settings/providers/{created['id']}/models/{model['id']}/health",
            json={},
        )

    asyncio.run(http_client.aclose())
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"
    assert response.json()["latency_ms"] >= 0
    assert json.loads(requests[0].content)["model"] == "team-fast"
    assert requests[0].headers["Authorization"] == "Bearer user-secret-key-value"
