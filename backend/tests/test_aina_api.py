from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi.testclient import TestClient

from tianzhou_agent_platform.aina.builtin import ensure_unibot_assistant, unibot_assistant_record
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.main import create_app
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
    )


def _manifest(*, permissions: list[str] | None = None) -> dict[str, Any]:
    return {
        "protocol_version": "1.0",
        "aina": {
            "id": "com.example.arithmetic",
            "name": "Arithmetic AINA",
            "version": "1.0.0",
            "description": "Performs deterministic arithmetic.",
            "publisher": {"id": "tests", "name": "Tests"},
        },
        "runtime": {
            "type": "remote",
            "endpoint": "https://aina.invalid/runtime",
            "streaming": False,
            "async_tasks": False,
        },
        "capabilities": {
            "skills": [
                {
                    "id": "multiply",
                    "name": "Multiply",
                    "description": "Multiply two numbers.",
                    "input_schema": {"type": "object"},
                }
            ],
            "tools": [],
            "ui": [],
            "events": [],
        },
        "permissions": permissions or [],
        "authentication": {"type": "none"},
    }


def test_register_install_and_automatically_invoke_remote_aina() -> None:
    invoked: list[dict[str, Any]] = []

    async def remote(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/describe"):
            return httpx.Response(200, json={"protocol_version": "1.0", "capabilities": {}})
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "healthy", "version": "1.0.0"})
        if request.url.path.endswith("/invoke"):
            payload = json.loads(request.content)
            invoked.append(payload)
            return httpx.Response(
                200,
                json={
                    "request_id": payload["request_id"],
                    "status": "completed",
                    "outputs": [{"type": "text", "content": "42"}],
                    "usage": {"input_tokens": 0, "output_tokens": 0},
                    "trace_id": payload["trace"]["trace_id"],
                },
            )
        return httpx.Response(404)

    llm = ScriptedLLM(
        [
            call_first_tool(arguments='{"input": "multiply 6 by 7"}', prefix="aina_"),
            assistant("The AINA result is 42."),
        ]
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        registered = client.post("/ainas", json=_manifest())
        installed = client.post("/ainas/com.example.arithmetic/install", json={})
        response = client.post("/chat", json={"message": "Please multiply 6 by 7"})
        trace = client.get(f"/traces/{response.json()['trace_id']}")
        uninstalled = client.delete("/ainas/com.example.arithmetic/install")

    assert registered.status_code == 201
    assert installed.status_code == 200
    assert response.status_code == 200
    assert response.json()["content"] == "The AINA result is 42."
    assert invoked[0]["input"] == {"input": "multiply 6 by 7"}
    assert invoked[0]["conversation_id"] == response.json()["conversation_id"]
    assert any(event["kind"] == "aina.completed" for event in trace.json()["events"])
    assert uninstalled.status_code == 204


def test_aina_without_required_grants_is_not_exposed_to_agent() -> None:
    async def remote(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/describe"):
            return httpx.Response(200, json={"protocol_version": "1.0"})
        return httpx.Response(200, json={"status": "healthy"})

    llm = ScriptedLLM([assistant("NO_AINA_MATCH"), assistant("No authorized AINA is available.")])
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(create_app(settings=_settings(), llm=llm, capability_http_client=capability_client)) as client:
        client.post("/ainas", json=_manifest(permissions=["user.files.read"]))
        client.post("/ainas/com.example.arithmetic/install", json={"granted_permissions": []})
        response = client.post("/chat", json={"message": "Use arithmetic"})

    assert response.status_code == 200
    candidate_names = {item["function"]["name"] for item in llm.calls[0]["tools"]}
    assert all(name.startswith("aina_") for name in candidate_names)
    assert not any(name.startswith("aina_com_example_arithmetic_") for name in candidate_names)


def test_aina_protocol_version_is_rejected_with_standard_error() -> None:
    llm = ScriptedLLM([])
    manifest = _manifest()
    manifest["protocol_version"] = "2.0"
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        response = client.post("/ainas", json=manifest)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"


def test_builtin_aina_manifests_expose_skill_prompts_and_tool_inputs() -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        records = {
            item["manifest"]["aina"]["id"]: item["manifest"]
            for item in client.get("/ainas").json()
        }

    memory = records["unibot-memory"]
    assert memory["capabilities"]["skills"][0]["instructions"]
    memory_tools = {item["id"]: item for item in memory["capabilities"]["tools"]}
    assert set(memory_tools["memory.remember"]["input_schema"]["properties"]) == {
        "content",
        "category",
    }
    assert memory_tools["memory.forget"]["input_schema"]["required"] == ["memory_id"]

    assistant = records["unibot-assistant"]
    assistant_tools = {item["id"]: item for item in assistant["capabilities"]["tools"]}
    assert assistant_tools["describe_aina"]["input_schema"]["required"] == ["aina_id"]
    assert assistant_tools["open_aina"]["input_schema"]["required"] == ["aina_id"]
    clarification = assistant_tools["request_clarification"]["input_schema"]
    assert clarification["required"] == ["title", "fields"]
    assert clarification["properties"]["fields"]["items"]["required"] == ["id", "label"]


async def test_builtin_aina_definition_is_refreshed_without_changing_registration_time() -> None:
    repository = InMemoryRepository()
    stale = unibot_assistant_record()
    stale.manifest.aina.name = "Legacy Assistant"
    await repository.register_aina(stale)

    await ensure_unibot_assistant(repository)

    refreshed = await repository.get_aina(stale.manifest.aina.id)
    assert refreshed.manifest.aina.name == "Unibot 助手"
    assert refreshed.registered_at == stale.registered_at


def test_conversation_soft_delete_and_restore() -> None:
    llm = ScriptedLLM([])
    with TestClient(create_app(settings=_settings(), llm=llm)) as client:
        created = client.post("/conversations", json={"title": "Restorable"}).json()
        deleted = client.delete(f"/conversations/{created['id']}")
        missing = client.get(f"/conversations/{created['id']}")
        restored = client.post(f"/conversations/{created['id']}/restore")

    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert restored.status_code == 200
    assert restored.json()["status"] == "active"
