from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.main import create_app


def _settings(**overrides: object) -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        auth_secret=SecretStr("test-auth-secret-with-enough-entropy"),
        **overrides,
    )


def _register(client: TestClient, *, email: str, name: str = "测试用户") -> dict[str, object]:
    response = client.post(
        "/auth/register",
        json={"name": name, "email": email, "password": "correct-horse-battery"},
    )
    assert response.status_code == 201
    return response.json()["user"]


def test_password_authentication_session_and_logout() -> None:
    repository = InMemoryRepository()
    app = create_app(settings=_settings(), repository=repository, enforce_auth=True)

    with TestClient(app) as client:
        assert client.get("/auth/config").json() == {
            "auth_required": True,
            "registration_enabled": True,
            "github_enabled": False,
        }
        assert client.get("/conversations").status_code == 401

        user = _register(client, email="USER@example.com")
        assert user["email"] == "user@example.com"
        assert user["providers"] == ["password"]
        assert user["is_admin"] is False
        assert "password" not in user
        assert client.get("/auth/me").json()["user"]["id"] == user["id"]

        duplicate = client.post(
            "/auth/register",
            json={"name": "重复用户", "email": "user@example.com", "password": "another-password"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["user_message"] == "该邮箱已注册。"

        logout = client.post("/auth/logout")
        assert logout.status_code == 204
        assert client.get("/auth/me").status_code == 401

        rejected = client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "wrong-password"},
        )
        assert rejected.status_code == 401

        accepted = client.post(
            "/auth/login",
            json={"email": "user@example.com", "password": "correct-horse-battery"},
        )
        assert accepted.status_code == 200
        assert accepted.json()["user"]["id"] == user["id"]


def test_authenticated_actor_cannot_impersonate_or_read_another_user() -> None:
    app = create_app(settings=_settings(), enforce_auth=True)

    with TestClient(app) as client:
        owner = _register(client, email="owner@example.com", name="Owner")
        conversation = client.post(
            "/conversations",
            json={"title": "Owner only", "user_id": "victim", "tenant_id": "other"},
        )
        assert conversation.status_code == 201
        assert conversation.json()["user_id"] == owner["id"]
        assert conversation.json()["tenant_id"] == owner["tenant_id"]

        second = _register(client, email="second@example.com", name="Second")
        assert second["id"] != owner["id"]
        assert client.get(f"/conversations/{conversation.json()['id']}").status_code == 403
        assert client.get("/conversations", params={"user_id": owner["id"]}).json() == []

        chat = client.post(
            "/chat",
            json={"message": "try to append", "conversation_id": conversation.json()["id"]},
        )
        assert chat.status_code == 403


def test_github_oauth_uses_state_pkce_and_creates_a_session() -> None:
    requests: list[httpx.Request] = []

    def github(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/login/oauth/access_token":
            body = parse_qs(request.content.decode())
            assert body["client_id"] == ["github-client"]
            assert body["client_secret"] == ["github-secret"]
            assert body["code"] == ["github-code"]
            assert body["code_verifier"][0]
            return httpx.Response(200, json={"access_token": "short-lived-token"})
        if request.url.path == "/user":
            assert request.headers["Authorization"] == "Bearer short-lived-token"
            return httpx.Response(
                200,
                json={
                    "id": 123456,
                    "login": "octocat",
                    "name": "The Octocat",
                    "avatar_url": "https://avatars.example/octocat.png",
                },
            )
        if request.url.path == "/user/emails":
            return httpx.Response(
                200,
                json=[{"email": "octocat@example.com", "primary": True, "verified": True}],
            )
        raise AssertionError(f"Unexpected GitHub request: {request.url}")

    github_client = httpx.AsyncClient(transport=httpx.MockTransport(github))
    settings = _settings(
        github_oauth_client_id="github-client",
        github_oauth_client_secret=SecretStr("github-secret"),
        github_oauth_callback_url="http://127.0.0.1:5173/api/auth/github/callback",
        frontend_base_url="http://127.0.0.1:5173",
    )
    app = create_app(
        settings=settings,
        github_auth_http_client=github_client,
        enforce_auth=True,
    )

    with TestClient(app) as client:
        start = client.get("/auth/github", params={"next": "/apps"}, follow_redirects=False)
        assert start.status_code == 302
        authorization = urlparse(start.headers["location"])
        query = parse_qs(authorization.query)
        assert authorization.netloc == "github.com"
        assert query["client_id"] == ["github-client"]
        assert query["scope"] == ["user:email"]
        assert query["code_challenge_method"] == ["S256"]
        assert query["code_challenge"][0]

        callback = client.get(
            "/auth/github/callback",
            params={"code": "github-code", "state": query["state"][0]},
            follow_redirects=False,
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == "http://127.0.0.1:5173/apps"
        me = client.get("/auth/me")
        assert me.status_code == 200
        assert me.json()["user"] == {
            "id": me.json()["user"]["id"],
            "email": "octocat@example.com",
            "name": "The Octocat",
            "avatar_url": "https://avatars.example/octocat.png",
            "tenant_id": "default",
            "providers": ["github"],
            "is_admin": False,
        }

    asyncio.run(github_client.aclose())
    assert [request.url.path for request in requests] == ["/login/oauth/access_token", "/user", "/user/emails"]


def test_platform_admin_allowlist_protects_admin_data() -> None:
    app = create_app(
        settings=_settings(admin_identities="admin@example.com"),
        enforce_auth=True,
    )

    with TestClient(app) as client:
        member = _register(client, email="member@example.com")
        assert member["is_admin"] is False
        member_conversation = client.post("/conversations", json={"title": "Member conversation"})
        assert member_conversation.status_code == 201
        denied = client.get("/admin/summary")
        assert denied.status_code == 403
        assert denied.json()["error"]["code"] == "PERMISSION_DENIED"

        assert client.post("/auth/logout").status_code == 204
        admin = _register(client, email="ADMIN@example.com")
        assert admin["is_admin"] is True
        admin_conversation = client.post("/conversations", json={"title": "Admin conversation"})
        assert admin_conversation.status_code == 201
        assert len(client.get("/conversations").json()) == 1
        assert client.get("/admin/summary").json()["conversations"] == 2
        assert len(client.get("/admin/conversations").json()) == 2
        assert client.get("/admin/traces").status_code == 200
        assert client.get("/admin/llm-calls").status_code == 200


def test_github_oauth_rejects_a_mismatched_state_before_token_exchange() -> None:
    requests: list[httpx.Request] = []

    def github(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    github_client = httpx.AsyncClient(transport=httpx.MockTransport(github))
    app = create_app(
        settings=_settings(
            github_oauth_client_id="github-client",
            github_oauth_client_secret=SecretStr("github-secret"),
        ),
        github_auth_http_client=github_client,
        enforce_auth=True,
    )

    with TestClient(app) as client:
        start = client.get("/auth/github", follow_redirects=False)
        assert start.status_code == 302
        rejected = client.get(
            "/auth/github/callback",
            params={"code": "github-code", "state": "attacker-state"},
            follow_redirects=False,
        )
        assert rejected.status_code == 401

    asyncio.run(github_client.aclose())
    assert requests == []


@pytest.mark.asyncio
async def test_github_identity_does_not_auto_link_an_unverified_local_email() -> None:
    repository = InMemoryRepository()
    await repository.create_user(
        UserRecord(
            id="user_local",
            email="shared@example.com",
            name="Local user",
            password_hash="an-existing-password-hash",
        )
    )

    with pytest.raises(PlatformError) as error:
        await repository.upsert_github_user(
            github_id="123456",
            github_login="octocat",
            email="shared@example.com",
            name="The Octocat",
            avatar_url=None,
        )

    assert error.value.status_code == 409
    assert error.value.user_message == "该邮箱已有账户，请先使用邮箱登录。"
