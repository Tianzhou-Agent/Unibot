from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.fake_llm import ScriptedLLM, assistant

from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.aina.protocol.models import AinaManifest
from tianzhou_agent_platform.aina.security.access import capability_visible
from tianzhou_agent_platform.aina.security.destinations import require_approved_destination
from tianzhou_agent_platform.aina.skill.models import SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.main import create_app


TOOL = {
    "tool_id": "private-tool", "name": "Private tool", "description": "Owner only",
    "input_schema": {"type": "object"}, "endpoint": "https://approved.invalid/invoke",
    "visibility": "private",
}
SKILL = {
    "skill_id": "private-skill", "name": "Private skill", "description": "Owner only",
    "instructions": "Use the private tool.", "visibility": "private", "status": "published",
}


def _manifest(**overrides) -> dict:
    return {
        "protocol_version": "1.0",
        "aina": {"id": "remote", "name": "Remote", "version": "1.0.0",
                 "description": "Test remote", "publisher": {"id": "test", "name": "Test"}},
        "runtime": {"type": "remote", "endpoint": "https://approved.invalid"},
        **overrides,
    }


def _register(client: TestClient, email: str) -> dict:
    response = client.post("/auth/register", json={
        "email": email, "name": "Test user", "password": "correct-horse-battery",
    })
    assert response.status_code == 201
    return response.json()["user"]


def test_claimed_admin_email_and_login_never_grant_admin() -> None:
    settings = AgentSettings(_env_file=None, admin_identities="admin@example.com,octocat")
    app = create_app(settings=settings, enforce_auth=True)
    with TestClient(app) as client:
        user = _register(client, "admin@example.com")
        assert user["is_admin"] is False
        assert client.get("/admin/summary").status_code == 403
        assert not settings.is_platform_admin(user_id=user["id"], email=user["email"], github_login="octocat")
        settings.admin_identities = user["id"]
        assert client.get("/auth/me").json()["user"]["is_admin"] is True
        assert client.get("/admin/summary").status_code == 200


def test_registry_permissions_and_server_bound_ownership() -> None:
    requests = []

    def remote(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "ok"})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    settings = AgentSettings(_env_file=None, capability_allowed_origins="https://approved.invalid")
    repository = InMemoryRepository()
    app = create_app(settings=settings, repository=repository, capability_http_client=http_client, enforce_auth=True)
    with TestClient(app) as client:
        owner = _register(client, "owner@example.com")
        for route, payload in [("tools", TOOL), ("skills", SKILL), ("ainas", _manifest())]:
            assert client.post(f"/{route}", json=payload).status_code == 403
            assert client.delete(f"/{route}/missing").status_code == 403
        assert not requests

        settings.admin_identities = owner["id"]
        for route, payload in [("tools", TOOL), ("skills", SKILL)]:
            assert client.post(f"/{route}", json={**payload, "owner_user_id": "victim"}).status_code == 422
            created = client.post(f"/{route}", json=payload)
            assert created.status_code == 201
            assert created.json()["owner_user_id"] == owner["id"]
            assert created.json()["tenant_id"] == owner["tenant_id"]
        assert client.post("/ainas", json=_manifest()).status_code == 201
        assert len(requests) == 2

        settings.admin_identities = ""
        for route, record_id in [("tools", TOOL["tool_id"]), ("skills", SKILL["skill_id"])]:
            assert client.get(f"/{route}/{record_id}").status_code == 200
            assert client.delete(f"/{route}/{record_id}").status_code == 403

        _register(client, "other@example.com")
        for route, record_id in [("tools", TOOL["tool_id"]), ("skills", SKILL["skill_id"])]:
            assert client.get(f"/{route}/{record_id}").status_code == 404
            assert client.get(f"/{route}").json() == []
            assert client.delete(f"/{route}/{record_id}").status_code == 403
        # Public discovery and installation remain regular-user operations.
        assert client.get("/ainas/remote").status_code == 200
        assert client.post("/ainas/remote/install", json={}).status_code == 200
        assert client.delete("/ainas/remote/install").status_code == 204
    asyncio.run(http_client.aclose())


@pytest.mark.parametrize("model,payload", [(ToolRecord, TOOL), (SkillRecord, SKILL)])
def test_visibility_boundary_includes_tenant_and_legacy_records(model, payload) -> None:
    record = model(**payload)
    assert not capability_visible(record, user_id="owner", tenant_id="tenant-a")
    assert capability_visible(record, user_id="any", tenant_id="any", is_admin=True)
    assert capability_visible(record, user_id="any", tenant_id="any", auth_enforced=False)
    record.owner_user_id, record.tenant_id = "owner", "tenant-a"
    assert capability_visible(record, user_id="owner", tenant_id="tenant-a")
    assert not capability_visible(record, user_id="other", tenant_id="tenant-a")
    record.visibility = "tenant"
    assert capability_visible(record, user_id="other", tenant_id="tenant-a")
    assert not capability_visible(record, user_id="owner", tenant_id="tenant-b")
    record.visibility = "public"
    assert capability_visible(record, user_id="other", tenant_id="tenant-b")


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://169.254.169.254/", "http://[::1]/",
    "https://approved.invalid.attacker.invalid/", "https://approved.invalid:444/",
    "http://approved.invalid/", "https://name:password@approved.invalid/",
])
def test_destination_policy_matches_exact_origins(url) -> None:
    with pytest.raises(PlatformError, match="not an approved"):
        require_approved_destination(url, "https://approved.invalid")


def test_internal_destinations_require_explicit_approval() -> None:
    require_approved_destination("http://127.0.0.1:8080/health", "http://127.0.0.1:8080")
    require_approved_destination("https://APPROVED.invalid:443/invoke", "https://approved.invalid")
    with pytest.raises(PlatformError):
        require_approved_destination("https://approved.invalid/invoke", "")


@pytest.mark.asyncio
async def test_endpoint_and_health_are_validated_before_registration_probes() -> None:
    requests = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: requests.append(request))) as client:
        gateway = RemoteCapabilityGateway(
            AgentSettings(_env_file=None, capability_allowed_origins="https://approved.invalid"), client,
        )
        for payload in [
            _manifest(runtime={"type": "remote", "endpoint": "http://127.0.0.1"}),
            _manifest(health_check="http://169.254.169.254/latest/meta-data"),
        ]:
            with pytest.raises(PlatformError, match="not an approved"):
                await gateway.probe_aina(AinaManifest.model_validate(payload))
        assert not requests


@pytest.mark.asyncio
async def test_execution_blocks_legacy_tools_and_redirected_destinations() -> None:
    requests = []

    def remote(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "http://169.254.169.254/marker"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(remote), follow_redirects=True) as client:
        gateway = RemoteCapabilityGateway(
            AgentSettings(_env_file=None, capability_allowed_origins="https://approved.invalid"), client,
        )
        for endpoint in ["http://127.0.0.1/invoke", "https://approved.invalid/invoke"]:
            tool = ToolRecord(**{**TOOL, "endpoint": endpoint})
            with pytest.raises(PlatformError, match="not an approved"):
                await gateway.invoke_tool(tool, arguments={}, call_id="call", user_id="owner",
                                          tenant_id="default", conversation_id="conv", trace_id="trace")
        assert [str(request.url) for request in requests] == ["https://approved.invalid/invoke"]


@pytest.mark.asyncio
async def test_a2a_discovered_interfaces_require_approval() -> None:
    requests = []

    def remote(request):
        requests.append(request)
        return httpx.Response(200, json={
            "name": "Agent", "description": "Test", "version": "1.0",
            "supportedInterfaces": [{"url": "http://169.254.169.254/invoke",
                                     "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
            "capabilities": {}, "defaultInputModes": ["text/plain"],
            "defaultOutputModes": ["text/plain"], "skills": [],
        })

    async with httpx.AsyncClient(transport=httpx.MockTransport(remote)) as client:
        gateway = RemoteCapabilityGateway(
            AgentSettings(_env_file=None, capability_allowed_origins="https://approved.invalid"), client,
        )
        with pytest.raises(PlatformError, match="not an approved"):
            await gateway.probe_aina(AinaManifest.model_validate(_manifest(
                runtime={"type": "remote", "endpoint": "https://approved.invalid", "protocol": "a2a"},
            )))
        assert len(requests) == 1
        assert requests[0].url.path == "/.well-known/agent-card.json"


@pytest.mark.parametrize("trusted", [False, True])
def test_authenticated_local_execution_requires_explicit_opt_in(tmp_path, trusted) -> None:
    app = create_app(settings=AgentSettings(
        _env_file=None, sandbox_workspace_root=tmp_path, sandbox_allow_unsafe_local=trusted,
    ), enforce_auth=True)
    with TestClient(app) as client:
        _register(client, "runner@example.com")
        response = client.post("/sandboxes/execute", json={"language": "python", "script": "print('safe marker')"})
        if trusted:
            assert response.status_code == 200
            assert response.json()["stdout"].strip() == "safe marker"
        else:
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "PERMISSION_DENIED"


def test_runtime_hides_private_tools_and_skill_instructions_from_other_users():
    llm = ScriptedLLM([assistant("Hello")])
    settings = AgentSettings(_env_file=None, capability_allowed_origins="https://approved.invalid")
    with TestClient(create_app(settings=settings, llm=llm, enforce_auth=True)) as client:
        owner = _register(client, "private-owner@example.com")
        settings.admin_identities = owner["id"]
        assert client.post("/tools", json=TOOL).status_code == 201
        assert client.post("/skills", json=SKILL).status_code == 201
        _register(client, "private-other@example.com")
        assert client.post("/chat", json={"message": "Hello"}).status_code == 200
        assert all("private_tool" not in definition["function"]["name"] for definition in llm.calls[0]["tools"])
        assert SKILL["instructions"] not in llm.calls[0]["messages"][0]["content"]
        rejected = client.post("/chat", json={"message": "Use it", "capability": "tool:private-tool"})
        assert rejected.status_code == 403
        assert len(llm.calls) == 1
