from fastapi import Request
from fastapi.testclient import TestClient

from tianzhou_agent_platform.api.dependencies import RequestActor, request_actor
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tests.support.fake_llm import ScriptedLLM


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
    )


def _scaffold(client: TestClient) -> bytes:
    response = client.post(
        "/aina-projects/scaffold",
        json={
            "aina_id": "com.example.actor-test",
            "name": "Actor Test AINA",
            "description": "Verifies trusted project ownership.",
            "language": "python",
        },
    )
    assert response.status_code == 200
    return response.content


def test_project_api_ignores_client_supplied_actor_query() -> None:
    app = create_app(settings=_settings(), llm=ScriptedLLM([]))

    with TestClient(app) as client:
        archive = _scaffold(client)
        imported = client.post(
            "/aina-projects?user_id=forged-user&tenant_id=forged-tenant",
            files={"file": ("actor-test.zip", archive, "application/zip")},
        )
        listed = client.get("/aina-projects?user_id=another-user&tenant_id=another-tenant")

    assert imported.status_code == 201
    assert imported.json()["user_id"] == "anonymous"
    assert imported.json()["tenant_id"] == "default"
    assert listed.json() == [imported.json()]


def test_project_api_uses_trusted_request_actor_for_ownership() -> None:
    app = create_app(settings=_settings(), llm=ScriptedLLM([]))

    def actor_from_test_middleware(request: Request) -> RequestActor:
        return RequestActor(
            user_id=request.headers.get("x-test-user", "anonymous"),
            tenant_id=request.headers.get("x-test-tenant", "default"),
        )

    app.dependency_overrides[request_actor] = actor_from_test_middleware
    owner_headers = {"x-test-user": "owner", "x-test-tenant": "tenant-a"}
    attacker_headers = {"x-test-user": "attacker", "x-test-tenant": "tenant-a"}

    with TestClient(app) as client:
        archive = _scaffold(client)
        imported = client.post(
            "/aina-projects",
            headers=owner_headers,
            files={"file": ("actor-test.zip", archive, "application/zip")},
        )
        project_id = imported.json()["id"]
        attacker_list = client.get("/aina-projects", headers=attacker_headers)
        attacker_download = client.get(
            f"/aina-projects/{project_id}/archive",
            headers=attacker_headers,
        )
        attacker_delete = client.delete(f"/aina-projects/{project_id}", headers=attacker_headers)
        owner_download = client.get(
            f"/aina-projects/{project_id}/archive",
            headers=owner_headers,
        )

    assert imported.status_code == 201
    assert imported.json()["user_id"] == "owner"
    assert attacker_list.json() == []
    assert attacker_download.status_code == 403
    assert attacker_delete.status_code == 403
    assert owner_download.status_code == 200
    assert owner_download.content == archive
