"""OAuth MCP servers (MCP authorization spec): discovery, registration, per-user connection, refresh and reconnect."""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.fake_llm import ScriptedLLM
from tests.test_aina_host_api import AINA_ID, RecordingMcp, _call
from tests.test_aina_package import MCP_URL, _example_files, _example_server, _register, _set, _settings, _with_manifest
from tianzhou_agent_platform.aina.package.mcp_client import McpCallResult
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.main import create_app

ISSUER = "https://auth.example.com"
PRM_URL = "http://127.0.0.1:8765/.well-known/oauth-protected-resource/mcp"


class FakeAuthorizationServer:
    """An MCP server's protected resource metadata and its OAuth 2.1 authorization server."""

    def __init__(self, *, s256: bool = True, registration: bool = True) -> None:
        self.s256, self.registration = s256, registration
        self.challenges: dict[str, str] = {}  # code -> code_challenge, set when the user "approves"
        self.token_requests: list[dict[str, str]] = []
        self.registrations: list[dict[str, Any]] = []
        self.refresh_ok = True
        self.issued = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "POST" and url == MCP_URL:
            return httpx.Response(401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{PRM_URL}"'})
        if url == PRM_URL:
            return httpx.Response(
                200, json={"resource": MCP_URL, "authorization_servers": [ISSUER], "scopes_supported": ["expenses"]}
            )
        if url == f"{ISSUER}/.well-known/oauth-authorization-server":
            metadata: dict[str, Any] = {
                "issuer": ISSUER,
                "authorization_endpoint": f"{ISSUER}/authorize",
                "token_endpoint": f"{ISSUER}/token",
            }
            if self.s256:
                metadata["code_challenge_methods_supported"] = ["S256"]
            if self.registration:
                metadata["registration_endpoint"] = f"{ISSUER}/register"
            return httpx.Response(200, json=metadata)
        if url == f"{ISSUER}/register":
            import json

            self.registrations.append(json.loads(request.content))
            return httpx.Response(201, json={"client_id": "unibot-client"})
        if url == f"{ISSUER}/token":
            form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            self.token_requests.append(form)
            if form["grant_type"] == "authorization_code":
                verifier = form.get("code_verifier", "")
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
                if self.challenges.get(form["code"]) != challenge:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE failed"})
            elif not self.refresh_ok:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "refresh revoked"})
            self.issued += 1
            return httpx.Response(
                200,
                json={
                    "access_token": f"at{self.issued}",
                    "refresh_token": "rt",
                    "expires_in": 3600,
                    "token_type": "Bearer",
                },
            )
        return httpx.Response(404)


class TokenCheckingMcp(RecordingMcp):
    """Rejects revoked access tokens, as a real OAuth MCP server answers 401."""

    def __init__(self, servers: dict[str, Any]) -> None:
        super().__init__(servers)
        self.revoked: set[str] = set()

    async def call_tool(self, url: str, name: str, arguments: dict[str, Any], **kwargs: Any) -> McpCallResult:
        bearer = (kwargs.get("headers") or {}).get("Authorization", "").removeprefix("Bearer ")
        if bearer in self.revoked:
            self.call_headers.append(dict(kwargs["headers"]))
            raise PlatformError("AUTHENTICATION_FAILED", "401 from the MCP server", status_code=502, source="mcp")
        return await super().call_tool(url, name, arguments, **kwargs)


def _oauth_files() -> dict[str, bytes]:
    return _with_manifest(_example_files(), _set("tools.mcp.auth", {"type": "oauth2"}))


@pytest.fixture
def oauth() -> Any:
    authorization = FakeAuthorizationServer()
    server = _example_server()
    mcp = TokenCheckingMcp({MCP_URL: server.mcp})
    app = create_app(
        settings=_settings(),
        llm=ScriptedLLM([]),
        mcp_client=mcp,
        aina_oauth_http_client=httpx.AsyncClient(transport=httpx.MockTransport(authorization.handle)),
    )
    server.HOST_TRANSPORT = httpx.ASGITransport(app=app)
    with TestClient(app) as client:
        assert _register(client, _oauth_files()).status_code == 201
        yield client, authorization, mcp


def _installation(client: TestClient) -> dict[str, Any]:
    return next(item for item in client.get("/installations").json() if item["aina_id"] == AINA_ID)


def _connect(
    client: TestClient, authorization: FakeAuthorizationServer, *, return_to: str = "/plugin/x"
) -> httpx.Response:
    """Start the flow, let the fake server "approve" it, and follow the redirect back to Unibot."""
    url = client.post(f"/ainas/{AINA_ID}/oauth/authorize", json={"return_to": return_to}).json()["authorization_url"]
    query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
    authorization.challenges["code-1"] = query["code_challenge"]
    return client.get(
        "/aina-oauth/callback", params={"code": "code-1", "state": query["state"]}, follow_redirects=False
    )


def test_a_user_connects_their_account_before_the_aina_is_usable(oauth: Any) -> None:
    client, authorization, mcp = oauth
    installed = client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []}).json()
    blocked = client.post(f"/ainas/{AINA_ID}/app-tools/list_expenses", json={"arguments": {}})

    url = client.post(f"/ainas/{AINA_ID}/oauth/authorize", json={"return_to": "/plugin/x"}).json()["authorization_url"]
    query = {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}
    authorization.challenges["code-1"] = query["code_challenge"]
    back = client.get(
        "/aina-oauth/callback", params={"code": "code-1", "state": query["state"]}, follow_redirects=False
    )
    connected = _installation(client)
    listed = client.post(f"/ainas/{AINA_ID}/app-tools/list_expenses", json={"arguments": {}})

    assert installed["status"] == "authorization_required" and installed["oauth_connected"] is False
    assert blocked.status_code == 403
    assert url.startswith(f"{ISSUER}/authorize?")
    assert query["client_id"] == "unibot-client" and query["code_challenge_method"] == "S256"
    assert query["resource"] == MCP_URL and query["scope"] == "expenses"
    assert query["redirect_uri"] == "http://127.0.0.1:5173/api/aina-oauth/callback"
    assert authorization.registrations[0]["token_endpoint_auth_method"] == "none"
    assert back.status_code == 302
    assert back.headers["location"] == f"http://127.0.0.1:5173/plugin/x?aina_oauth=connected&aina={AINA_ID}"
    assert connected["status"] == "active" and connected["oauth_connected"] is True
    assert "@oauth" not in connected["configured_secrets"]
    assert listed.status_code == 200, listed.text
    assert mcp.call_headers[-1] == {"Authorization": "Bearer at1"}
    assert authorization.token_requests[0]["resource"] == MCP_URL


def test_a_rejected_token_is_refreshed_once_then_the_user_reconnects(oauth: Any) -> None:
    client, authorization, mcp = oauth
    client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []})
    _connect(client, authorization)

    mcp.revoked.add("at1")
    refreshed = client.post(f"/ainas/{AINA_ID}/app-tools/list_expenses", json={"arguments": {}})
    mcp.revoked.add("at2")
    authorization.refresh_ok = False
    failed = client.post(f"/ainas/{AINA_ID}/app-tools/list_expenses", json={"arguments": {}})
    paused = _installation(client)

    assert refreshed.status_code == 200, refreshed.text
    assert [request["grant_type"] for request in authorization.token_requests] == [
        "authorization_code",
        "refresh_token",
        "refresh_token",
    ]
    assert mcp.call_headers[-1] == {"Authorization": "Bearer at2"}
    assert failed.status_code == 409 and "connect" in failed.json()["error"]["message"]
    assert paused["status"] == "authorization_required" and paused["oauth_connected"] is False


def test_reinstalling_or_upgrading_keeps_the_connection(oauth: Any) -> None:
    client, authorization, _mcp = oauth
    client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []})
    _connect(client, authorization)

    reinstalled = client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": ["documents.read"]}).json()
    upgraded = _register(client, _with_manifest(_oauth_files(), _set("aina.version", "1.3.0")))
    listed = client.post(f"/ainas/{AINA_ID}/app-tools/list_expenses", json={"arguments": {}})

    assert reinstalled["status"] == "active" and reinstalled["oauth_connected"] is True
    assert (
        upgraded.status_code == 201 and upgraded.json()["manifest"]["runtime"]["oauth"]["client_id"] == "unibot-client"
    )
    assert listed.status_code == 200
    assert len(authorization.registrations) == 1  # Unibot registered once


def test_disconnecting_drops_the_tokens(oauth: Any) -> None:
    client, authorization, _mcp = oauth
    client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []})
    _connect(client, authorization)

    disconnected = client.post(f"/ainas/{AINA_ID}/oauth/disconnect").json()

    assert disconnected["status"] == "authorization_required" and disconnected["oauth_connected"] is False
    assert client.post(f"/ainas/{AINA_ID}/app-tools/list_expenses", json={"arguments": {}}).status_code == 403


def test_a_failed_or_forged_authorization_is_reported_back(oauth: Any) -> None:
    client, authorization, _mcp = oauth
    client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []})
    url = client.post(f"/ainas/{AINA_ID}/oauth/authorize", json={"return_to": "https://evil.example/x"}).json()[
        "authorization_url"
    ]
    state = parse_qs(urlsplit(url).query)["state"][0]

    denied = client.get(
        "/aina-oauth/callback", params={"error": "access_denied", "state": state}, follow_redirects=False
    )
    wrong_pkce = client.get("/aina-oauth/callback", params={"code": "unknown", "state": state}, follow_redirects=False)
    forged = client.get("/aina-oauth/callback", params={"code": "x", "state": "forged"}, follow_redirects=False)
    other_user = pytest.raises(PlatformError, match="another user")
    with other_user:
        _call(
            client,
            client.app.state.aina_packages.complete_authorization,
            code="x",
            state=state,
            tenant_id="t1",
            user_id="mallory",
        )  # type: ignore[attr-defined]

    # The return path stays on Unibot; failures carry the reason.
    assert denied.headers["location"].startswith("http://127.0.0.1:5173/plugin?aina_oauth=failed")
    assert "access_denied" in denied.headers["location"]
    assert "PKCE+failed" in wrong_pkce.headers["location"]
    assert forged.headers["location"].startswith("http://127.0.0.1:5173/plugin?aina_oauth=failed")
    assert _installation(client)["status"] == "authorization_required"


@pytest.mark.parametrize(
    ("server", "message"),
    [
        (FakeAuthorizationServer(s256=False), "PKCE"),
        (FakeAuthorizationServer(registration=False), "dynamic client registration"),
    ],
)
def test_servers_unibot_cannot_safely_use_are_refused(server: FakeAuthorizationServer, message: str) -> None:
    example = _example_server()
    app = create_app(
        settings=_settings(),
        llm=ScriptedLLM([]),
        mcp_client=RecordingMcp({MCP_URL: example.mcp}),
        aina_oauth_http_client=httpx.AsyncClient(transport=httpx.MockTransport(server.handle)),
    )
    with TestClient(app) as client:
        _register(client, _oauth_files())
        client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []})
        started = client.post(f"/ainas/{AINA_ID}/oauth/authorize", json={"return_to": "/plugin"})

    assert started.status_code == 502 and message in started.json()["error"]["message"]


def test_oauth_scopes_are_only_for_oauth_servers() -> None:
    from tianzhou_agent_platform.aina.package.validation import validate_package_files

    bearer = _with_manifest(
        _example_files(), _set("tools.mcp.auth", {"type": "bearer", "secret": "api_token", "scopes": ["x"]})
    )
    scoped = _with_manifest(_example_files(), _set("tools.mcp.auth", {"type": "oauth2", "scopes": ["expenses.read"]}))

    assert validate_package_files(bearer).errors
    assert validate_package_files(scoped).errors == []


def test_registration_explains_that_an_oauth_server_needs_an_access_token() -> None:
    class Unauthorized(RecordingMcp):
        async def list_tools(self, url: str, *, headers: Any = None, timeout: float = 30.0) -> Any:
            if url == MCP_URL and not (headers or {}).get("Authorization"):
                raise PlatformError("AUTHENTICATION_FAILED", "401", status_code=502, source="mcp")
            return await super().list_tools(url, headers=headers, timeout=timeout)

    mcp = Unauthorized({MCP_URL: _example_server().mcp})
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]), mcp_client=mcp)) as client:
        refused = _register(client, _oauth_files())
        archive = __import__("tianzhou_agent_platform.aina.project", fromlist=["x"]).build_project_archive(
            _oauth_files()
        )
        accepted = client.post(
            "/ainas/packages",
            files={"file": ("p.aina.zip", archive, "application/zip")},
            data={"credential": "admin-access-token"},
        )

    assert refused.status_code == 422 and "access token" in refused.json()["error"]["message"]
    assert accepted.status_code == 201
    assert mcp.list_headers[-1] == {"Authorization": "Bearer admin-access-token"}


def test_a_refused_http_request_is_an_authentication_failure_even_when_the_sdk_drops_the_status() -> None:
    """The MCP SDK reports a 401 as a bare ``MCPError``; the client keeps the response statuses it saw."""
    from mcp.shared.exceptions import MCPError

    from tianzhou_agent_platform.aina.package.mcp_client import _failure

    error = MCPError(-32603, "Server returned an error response")

    assert _failure(error, "failed", [401]).code == "AUTHENTICATION_FAILED"
    assert _failure(error, "failed", [200, 403]).code == "AUTHENTICATION_FAILED"
    assert _failure(error, "failed", [500]).code == "DEPENDENCY_FAILED"
    assert _failure(error, "failed").code == "DEPENDENCY_FAILED"
