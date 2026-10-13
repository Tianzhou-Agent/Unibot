from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient
from jose import jwt
from pydantic import SecretStr

from tests.test_auth import _settings
from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository


def _huawei_settings(**overrides: object) -> AgentSettings:
    return _settings(
        huawei_oauth_base_url="https://uniportal.example.com/",
        huawei_oauth_client_id="huawei-client",
        huawei_oauth_client_secret=SecretStr("huawei-secret"),
        **overrides,
    )


@pytest.mark.parametrize("frontend_path", ["", "/superbot"])
@pytest.mark.parametrize("next_path", ["/apps?tab=mine", "//attacker.example", "https://attacker.example", "/\\evil"])
def test_huawei_oauth_matches_uniportal_protocol_and_reuses_identity(frontend_path: str, next_path: str) -> None:
    requests: list[httpx.Request] = []
    profile = {"globalUserID": "global-123", "email": "Employee@example.com", "givenName": " Employee "}

    def huawei(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/saaslogin1/oauth2/accesstoken":
            assert request.method == "POST"
            assert request.headers["Content-Type"] == "application/json"
            assert json.loads(request.content) == {
                "client_id": "huawei-client",
                "client_secret": "huawei-secret",
                "code": "huawei-code",
                "grant_type": "authorization_code",
            }
            return httpx.Response(200, json={"access_token": "temporary-huawei-token"})
        assert request.url.path == "/saaslogin1/oauth2/userinfo"
        assert request.method == "GET"
        assert request.headers["Authorization"] == "Bearer temporary-huawei-token"
        assert dict(request.url.params) == {
            "access_token": "temporary-huawei-token",
            "client_id": "huawei-client",
            "scope": "base.profile",
        }
        return httpx.Response(200, json=profile)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(huawei))
    settings = _huawei_settings(
        huawei_oauth_callback_url=f"http://127.0.0.1:5173{frontend_path}/api/auth/huawei/callback",
        frontend_base_url=f"http://127.0.0.1:5173{frontend_path}",
    )
    repository = InMemoryRepository()
    app = create_app(settings=settings, repository=repository, huawei_auth_http_client=http_client, enforce_auth=True)
    with TestClient(app) as client:
        assert client.get("/auth/config").json()["huawei_enabled"] is True
        user_id = None
        for attempt in range(2):
            start = client.get("/auth/huawei", params={"next": next_path}, follow_redirects=False)
            assert start.status_code == 302
            authorization = urlparse(start.headers["location"])
            query = parse_qs(authorization.query)
            assert authorization.netloc == "uniportal.example.com"
            assert authorization.path == "/saaslogin1/oauth2/authorize"
            assert query["client_id"] == ["huawei-client"]
            assert query["redirect_uri"] == [settings.huawei_oauth_callback_url]
            assert query["scope"] == ["base.profile"]
            assert query["response_type"] == ["code"]
            assert "HttpOnly" in start.headers["set-cookie"]
            assert "Max-Age=600" in start.headers["set-cookie"]
            callback = client.get(
                "/auth/huawei/callback",
                params={"code": "huawei-code", "state": query["state"][0]},
                follow_redirects=False,
            )
            assert callback.status_code == 302
            safe_path = next_path if next_path == "/apps?tab=mine" else "/chat"
            assert callback.headers["location"] == f"http://127.0.0.1:5173{frontend_path}{safe_path}"
            assert "unibot_huawei_oauth" not in client.cookies
            assert "HttpOnly" in callback.headers["set-cookie"]
            me = client.get("/auth/me").json()["user"]
            assert me["email"] == "employee@example.com"
            assert me["name"] == ("Employee" if attempt == 0 else "Updated employee")
            assert me["providers"] == ["huawei"]
            assert me["is_admin"] is False
            assert "huawei_id" not in me
            if user_id is not None:
                assert me["id"] == user_id
            user_id = me["id"]
            assert client.get("/conversations").status_code == 200
            assert client.post("/auth/logout").status_code == 204
            assert client.get("/auth/me").status_code == 401
            profile.update(email="changed@example.com", givenName="Updated employee")
    asyncio.run(http_client.aclose())
    assert len(requests) == 4
    users = asyncio.run(repository.list_users())
    assert len(users) == 1
    assert users[0].huawei_id == "global-123"
    assert "temporary-huawei-token" not in users[0].model_dump_json()


@pytest.mark.parametrize(
    "failure",
    ["state", "missing-cookie", "tampered-cookie", "expired-cookie", "github-cookie", "error", "missing-code"],
)
def test_huawei_rejects_invalid_callback_before_token_exchange(failure: str) -> None:
    requests: list[httpx.Request] = []

    def huawei(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(huawei))
    settings = _huawei_settings()
    app = create_app(settings=settings, huawei_auth_http_client=http_client, enforce_auth=True)
    with TestClient(app) as client:
        start = client.get("/auth/huawei", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        params = {"code": "huawei-code", "state": state}
        if failure == "state":
            params["state"] = "attacker-state"
        elif failure in {"missing-cookie", "tampered-cookie", "expired-cookie", "github-cookie"}:
            token = client.cookies.get("unibot_huawei_oauth")
            client.cookies.clear()
            if failure != "missing-cookie":
                claims = jwt.decode(token, settings.auth_secret.get_secret_value(), algorithms=["HS256"])
                if failure == "expired-cookie":
                    claims["exp"] = datetime.now(UTC) - timedelta(minutes=1)
                if failure == "github-cookie":
                    claims["typ"] = "github-oauth"
                token = jwt.encode(claims, settings.auth_secret.get_secret_value(), algorithm="HS256")
                client.cookies.set("unibot_huawei_oauth", "invalid" if failure == "tampered-cookie" else token)
        elif failure == "error":
            params["error"] = "access_denied"
        else:
            params.pop("code")
        rejected = client.get("/auth/huawei/callback", params=params, follow_redirects=False)
        assert rejected.status_code == 401
        assert "华为" in rejected.json()["error"]["user_message"]
        assert client.get("/auth/me").status_code == 401
    asyncio.run(http_client.aclose())
    assert requests == []


@pytest.mark.parametrize(
    "failure",
    [
        "token-http",
        "token-json",
        "token-error",
        "token-empty",
        "profile-http",
        "profile-list",
        "profile-error",
        "missing-id",
        "missing-email",
        "invalid-email",
        "timeout",
    ],
)
def test_huawei_rejects_upstream_errors_and_invalid_profiles(failure: str) -> None:
    def huawei(request: httpx.Request) -> httpx.Response:
        if failure == "timeout":
            raise httpx.ReadTimeout("timeout", request=request)
        if request.url.path.endswith("accesstoken"):
            if failure == "token-http":
                return httpx.Response(503)
            if failure == "token-json":
                return httpx.Response(200, text="invalid json")
            if failure == "token-error":
                return httpx.Response(200, json={"error": "invalid_grant", "access_token": "token"})
            return httpx.Response(200, json={} if failure == "token-empty" else {"access_token": "token"})
        profile = {"globalUserID": "global-123", "email": "employee@example.com", "givenName": "Employee"}
        if failure == "profile-http":
            return httpx.Response(500)
        if failure == "profile-list":
            return httpx.Response(200, json=[])
        if failure == "profile-error":
            profile["error"] = "invalid_token"
        if failure == "missing-id":
            profile.pop("globalUserID")
        if failure == "missing-email":
            profile.pop("email")
        if failure == "invalid-email":
            profile["email"] = "invalid"
        return httpx.Response(200, json=profile)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(huawei))
    app = create_app(settings=_huawei_settings(), huawei_auth_http_client=http_client, enforce_auth=True)
    with TestClient(app) as client:
        start = client.get("/auth/huawei", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        response = client.get(
            "/auth/huawei/callback",
            params={"code": "code", "state": state},
            follow_redirects=False,
        )
        assert response.status_code == 401
        assert client.get("/auth/me").status_code == 401
    asyncio.run(http_client.aclose())


@pytest.mark.parametrize("missing", ["huawei_oauth_base_url", "huawei_oauth_client_id", "huawei_oauth_client_secret"])
@pytest.mark.parametrize("empty", [None, ""])
def test_huawei_requires_all_configuration(missing: str, empty: str | None) -> None:
    settings = _huawei_settings()
    value = SecretStr(empty) if missing == "huawei_oauth_client_secret" and empty is not None else empty
    setattr(settings, missing, value)
    app = create_app(settings=settings, enforce_auth=True)
    with TestClient(app) as client:
        assert client.get("/auth/config").json()["huawei_enabled"] is False
        assert client.get("/auth/huawei", follow_redirects=False).status_code == 503


def test_huawei_does_not_auto_link_existing_email() -> None:
    repository = InMemoryRepository()
    asyncio.run(repository.create_user(UserRecord(id="local", email="shared@example.com", name="Local")))

    def huawei(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=(
                {"access_token": "token"}
                if request.url.path.endswith("accesstoken")
                else {
                    "globalUserID": "global-123",
                    "email": "SHARED@example.com",
                    "givenName": "Employee",
                }
            ),
        )

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(huawei))
    app = create_app(
        settings=_huawei_settings(), repository=repository, huawei_auth_http_client=http_client, enforce_auth=True
    )
    with TestClient(app) as client:
        start = client.get("/auth/huawei", follow_redirects=False)
        state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
        response = client.get("/auth/huawei/callback", params={"code": "code", "state": state}, follow_redirects=False)
        assert response.status_code == 409
        assert client.get("/auth/me").status_code == 401
    asyncio.run(http_client.aclose())
    assert asyncio.run(repository.find_user_by_id("local")).huawei_id is None


def test_huawei_accepts_nexent_environment_names(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("BASE_URL", "CLIENT_ID", "CLIENT_SECRET"):
        monkeypatch.setenv(f"UNIBOT_HUAWEI_{name}", "")
    monkeypatch.setenv("UNIPORTAL_URL", "https://uniportal.example.com")
    monkeypatch.setenv("HUAWEI_OAUTH_CLIENT_ID", "nexent-client")
    monkeypatch.setenv("HUAWEI_OAUTH_CLIENT_SECRET", "nexent-secret")
    settings = AgentSettings(_env_file=None)
    assert settings.huawei_oauth_enabled is True
    assert settings.huawei_oauth_base_url == "https://uniportal.example.com"
    assert settings.huawei_oauth_client_id == "nexent-client"
    assert settings.huawei_oauth_client_secret.get_secret_value() == "nexent-secret"
    monkeypatch.setenv("UNIBOT_HUAWEI_CLIENT_ID", "unibot-client")
    assert AgentSettings(_env_file=None).huawei_oauth_client_id == "unibot-client"


def test_huawei_reads_nexent_aliases_from_dotenv_template(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "UNIBOT_HUAWEI_BASE_URL", "UNIBOT_HUAWEI_CLIENT_ID", "UNIBOT_HUAWEI_CLIENT_SECRET",
        "UNIPORTAL_URL", "HUAWEI_OAUTH_CLIENT_ID", "HUAWEI_OAUTH_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        'UNIBOT_HUAWEI_BASE_URL=""\nUNIBOT_HUAWEI_CLIENT_ID=""\nUNIBOT_HUAWEI_CLIENT_SECRET=""\n'
        'UNIPORTAL_URL="https://uniportal.example.com"\nHUAWEI_OAUTH_CLIENT_ID="nexent-client"\n'
        'HUAWEI_OAUTH_CLIENT_SECRET="nexent-secret"\n',
        encoding="utf-8",
    )
    settings = AgentSettings(_env_file=dotenv)
    assert settings.huawei_oauth_enabled is True
    assert settings.huawei_oauth_base_url == "https://uniportal.example.com"
    assert settings.huawei_oauth_client_id == "nexent-client"
    assert settings.huawei_oauth_client_secret.get_secret_value() == "nexent-secret"
    assert "nexent-secret" not in settings.model_dump_json()
