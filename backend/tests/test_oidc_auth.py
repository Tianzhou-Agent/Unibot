from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jose import jwk, jwt
from pydantic import SecretStr

from tests.test_auth import _settings
from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository

PROVIDERS = ["google", "microsoft", "apple"]
TENANT = "11111111-2222-3333-4444-555555555555"


@pytest.fixture(scope="module")
def signing_keys():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key = {**jwk.construct(public, algorithm="RS256").to_dict(), "kid": "test-key", "use": "sig"}
    return private, key


def _callback(provider, client, params, user=None):
    if provider == "apple":
        if user is not None:
            params = {**params, "user": json.dumps(user)}
        return client.post("/auth/apple/callback", data=params, follow_redirects=False)
    return client.get(f"/auth/{provider}/callback", params=params, follow_redirects=False)


@pytest.fixture(params=PROVIDERS)
def oidc(request, signing_keys):
    provider = request.param
    private, key = signing_keys
    settings = _settings(
        **{
            f"{provider}_oauth_client_id": f"{provider}-client",
            f"{provider}_oauth_client_secret": SecretStr(f"{provider}-secret"),
            f"{provider}_oauth_callback_url": f"https://unibot.example/superbot/api/auth/{provider}/callback",
            "frontend_base_url": "https://unibot.example/superbot",
            "auth_cookie_secure": True,
        }
    )
    profile = {
        "iss": {
            "google": "https://accounts.google.com",
            "microsoft": f"https://login.microsoftonline.com/{TENANT}/v2.0",
            "apple": "https://appleid.apple.com",
        }[provider],
        "aud": f"{provider}-client",
        "sub": "stable-subject",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        "email": "Employee@example.com",
        "email_verified": "true",
        "nonce": "pending",
    }
    if provider == "microsoft":
        profile["tid"] = TENANT
    if provider != "apple":
        profile["name"] = "Employee"
        profile["at_hash"] = (
            base64.urlsafe_b64encode(hashlib.sha256(b"access-token").digest()[:16]).rstrip(b"=").decode()
        )
    if provider == "google":
        profile["picture"] = "https://avatars.example/employee.png"
    keys = {"keys": [{**key, "issuer": "https://login.microsoftonline.com/{tenantid}/v2.0"}]}
    authorization = {}
    requests = []
    fault = {}

    def provider_http(http_request):
        requests.append(http_request)
        if http_request.method == "POST":
            expected_path = {
                "google": "/token",
                "microsoft": f"/{settings.microsoft_oauth_tenant}/oauth2/v2.0/token",
                "apple": "/auth/token",
            }[provider]
            assert http_request.url.path == expected_path
            assert http_request.headers["Content-Type"] == "application/x-www-form-urlencoded"
            body = parse_qs(http_request.content.decode())
            assert body["client_id"] == [f"{provider}-client"]
            assert body["client_secret"] == [f"{provider}-secret"]
            assert body["redirect_uri"] == [getattr(settings, f"{provider}_oauth_callback_url")]
            assert body["grant_type"] == ["authorization_code"]
            assert body["code"] == ["provider-code"]
            if provider != "apple":
                challenge = (
                    base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"][0].encode()).digest())
                    .rstrip(b"=")
                    .decode()
                )
                assert authorization["code_challenge"] == [challenge]
            else:
                assert "code_verifier" not in body
            if fault.get("kind") == "timeout":
                raise httpx.ReadTimeout("timeout", request=http_request)
            if fault.get("kind") == "token-http":
                return httpx.Response(503)
            if fault.get("kind") == "token-json":
                return httpx.Response(200, text="invalid-json")
            if fault.get("kind") == "token-error":
                return httpx.Response(200, json={"error": "invalid_grant"})
            if fault.get("kind") == "missing-token":
                return httpx.Response(200, json={"access_token": "access-token"})
            algorithm, signing_key = ("HS256", "wrong-key") if fault.get("kind") == "algorithm" else ("RS256", private)
            token = jwt.encode(profile, signing_key, algorithm=algorithm, headers={"kid": "test-key"})
            if fault.get("kind") == "signature":
                token = token.rsplit(".", 1)[0] + ".invalid-signature"
            return httpx.Response(200, json={"id_token": token, "access_token": "access-token"})
        expected_path = {
            "google": "/oauth2/v3/certs",
            "microsoft": f"/{settings.microsoft_oauth_tenant}/discovery/v2.0/keys",
            "apple": "/auth/keys",
        }[provider]
        assert http_request.url.path == expected_path
        if fault.get("kind") == "keys-http":
            return httpx.Response(503)
        return httpx.Response(200, json=keys)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(provider_http))
    repository = InMemoryRepository()
    app = create_app(settings=settings, repository=repository, oidc_auth_http_client=http_client, enforce_auth=True)
    with TestClient(app, base_url="https://unibot.example") as client:

        def start(next_path="/apps?tab=mine"):
            response = client.get(f"/auth/{provider}", params={"next": next_path}, follow_redirects=False)
            assert response.status_code == 302
            authorization.clear()
            authorization.update(parse_qs(urlparse(response.headers["location"]).query))
            profile["nonce"] = authorization["nonce"][0]
            return response, authorization["state"][0]

        yield provider, client, start, profile, requests, fault, repository, settings, keys
    asyncio.run(http_client.aclose())


@pytest.mark.parametrize("next_path", ["/apps?tab=mine", "//evil.example", "https://evil.example", "/\\evil"])
def test_oidc_authorization_protocol_and_session_identity(oidc, next_path):
    provider, client, start, profile, requests, fault, repository, settings, keys = oidc
    assert client.get("/auth/config").json()[f"{provider}_enabled"] is True
    user_id = None
    for attempt in range(2):
        response, state = start(next_path)
        query = parse_qs(urlparse(response.headers["location"]).query)
        assert query["client_id"] == [f"{provider}-client"]
        assert query["redirect_uri"] == [getattr(settings, f"{provider}_oauth_callback_url")]
        assert query["response_type"] == ["code"]
        assert query["scope"] == (["name email"] if provider == "apple" else ["openid email profile"])
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Secure" in response.headers["set-cookie"]
        assert "Max-Age=600" in response.headers["set-cookie"]
        if provider == "apple":
            assert query["response_mode"] == ["form_post"]
            assert "SameSite=none" in response.headers["set-cookie"]
            assert "code_challenge" not in query
        else:
            assert query["code_challenge_method"] == ["S256"]
        apple_user = (
            {"name": {"firstName": "Employee", "lastName": ""}, "email": "untrusted@example.com"}
            if attempt == 0
            else None
        )
        callback = _callback(provider, client, {"code": "provider-code", "state": state}, user=apple_user)
        assert callback.status_code == 303
        path = next_path if next_path == "/apps?tab=mine" else "/chat"
        assert callback.headers["location"] == f"https://unibot.example/superbot{path}"
        assert f"unibot_{provider}_oauth" not in client.cookies
        user = client.get("/auth/me").json()["user"]
        assert user["email"] == "employee@example.com"
        assert user["name"] == "Employee"
        assert user["providers"] == [provider]
        assert user["is_admin"] is False
        assert "oidc_identities" not in user
        if user_id is not None:
            assert user["id"] == user_id
        user_id = user["id"]
        assert client.get("/conversations").status_code == 200
        assert client.post("/auth/logout").status_code == 204
        assert client.get("/auth/me").status_code == 401
        profile["email"] = "changed@example.com"
        profile.pop("name", None)
    assert len(requests) == 4
    users = asyncio.run(repository.list_users())
    assert len(users) == 1
    assert users[0].oidc_identities[provider].endswith("|stable-subject")
    assert "access-token" not in users[0].model_dump_json()
    # A completed callback cannot be replayed without the browser's pending cookie.
    assert _callback(provider, client, {"code": "provider-code", "state": state}).status_code == 401
    assert len(requests) == 4


@pytest.mark.parametrize(
    "kind",
    [
        "state",
        "unicode-state",
        "missing-cookie",
        "expired-cookie",
        "wrong-provider",
        "error",
        "missing-code",
        "missing-verifier",
    ],
)
def test_oidc_rejects_invalid_callbacks_before_exchange(oidc, kind):
    provider, client, start, profile, requests, fault, repository, settings, keys = oidc
    if kind == "missing-verifier" and provider == "apple":
        pytest.skip("Apple does not use PKCE")
    _, state = start()
    params = {"code": "provider-code", "state": state}
    if kind in {"state", "unicode-state"}:
        params["state"] = "攻击者" if kind == "unicode-state" else "attacker"
    elif kind in {"missing-cookie", "expired-cookie", "wrong-provider", "missing-verifier"}:
        cookie_name = f"unibot_{provider}_oauth"
        token = client.cookies.get(cookie_name)
        client.cookies.clear()
        if kind != "missing-cookie":
            claims = jwt.decode(token, settings.auth_secret.get_secret_value(), algorithms=["HS256"])
            if kind == "expired-cookie":
                claims["exp"] = int(time.time()) - 60
            elif kind == "wrong-provider":
                claims["typ"] = "github-oauth"
            else:
                claims.pop("verifier")
            client.cookies.set(
                cookie_name, jwt.encode(claims, settings.auth_secret.get_secret_value(), algorithm="HS256")
            )
    elif kind == "error":
        params["error"] = "access_denied"
    else:
        params.pop("code")
    response = _callback(provider, client, params)
    assert response.status_code == 401
    assert client.get("/auth/me").status_code == 401
    assert requests == []
    assert asyncio.run(repository.list_users()) == []


@pytest.mark.parametrize(
    "kind",
    [
        "nonce",
        "audience",
        "issuer",
        "expired",
        "missing-exp",
        "missing-sub",
        "unverified-email",
        "missing-email",
        "algorithm",
        "signature",
        "key",
        "token-http",
        "token-json",
        "token-error",
        "missing-token",
        "keys-http",
        "timeout",
        "access-hash",
        "authorized-party",
    ],
)
def test_oidc_rejects_invalid_id_tokens_and_upstream_failures(oidc, kind):
    provider, client, start, profile, requests, fault, repository, settings, keys = oidc
    if kind == "unverified-email" and provider == "microsoft":
        pytest.skip("Microsoft does not issue the email_verified claim")
    _, state = start()
    if kind == "nonce":
        profile["nonce"] = "wrong"
    elif kind == "audience":
        profile["aud"] = "different-client"
    elif kind == "issuer":
        profile["iss"] = "https://attacker.example"
    elif kind == "expired":
        profile["exp"] = int(time.time()) - 60
    elif kind == "missing-exp":
        profile.pop("exp")
    elif kind == "missing-sub":
        profile.pop("sub")
    elif kind == "unverified-email":
        profile["email_verified"] = False
    elif kind == "missing-email":
        profile.pop("email")
    elif kind == "key":
        keys["keys"] = []
    elif kind == "access-hash":
        profile["at_hash"] = "wrong"
    elif kind == "authorized-party":
        profile["azp"] = "wrong-client"
    else:
        fault["kind"] = kind
    assert _callback(provider, client, {"code": "provider-code", "state": state}).status_code == 401
    assert client.get("/auth/me").status_code == 401
    assert asyncio.run(repository.list_users()) == []


def test_oidc_never_auto_merges_existing_email(oidc):
    provider, client, start, profile, requests, fault, repository, settings, keys = oidc
    asyncio.run(repository.create_user(UserRecord(id="local", email="employee@example.com", name="Local")))
    _, state = start()
    assert _callback(provider, client, {"code": "provider-code", "state": state}).status_code == 409
    assert asyncio.run(repository.find_user_by_id("local")).oidc_identities == {}
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize("oidc", ["microsoft"], indirect=True)
@pytest.mark.parametrize(
    "configured,tenant",
    [
        ("common", "9188040d-6c67-4c5b-b112-36a304b66dad"),
        ("consumers", "9188040d-6c67-4c5b-b112-36a304b66dad"),
        ("organizations", TENANT),
        (TENANT, TENANT),
    ],
)
def test_microsoft_accepts_personal_and_allowed_organization_accounts(oidc, configured, tenant):
    provider, client, start, profile, requests, fault, repository, settings, keys = oidc
    settings.microsoft_oauth_tenant = configured
    profile["tid"] = tenant
    profile["iss"] = f"https://login.microsoftonline.com/{tenant}/v2.0"
    _, state = start()
    assert _callback(provider, client, {"code": "provider-code", "state": state}).status_code == 303
    assert client.get("/auth/me").json()["user"]["providers"] == ["microsoft"]


@pytest.mark.parametrize("provider", PROVIDERS)
def test_unconfigured_oidc_provider_is_hidden_and_unavailable(provider):
    with TestClient(create_app(settings=_settings(), enforce_auth=True)) as client:
        assert client.get("/auth/config").json()[f"{provider}_enabled"] is False
        assert client.get(f"/auth/{provider}", follow_redirects=False).status_code == 503


@pytest.mark.parametrize(
    "settings", [{"auth_cookie_secure": False}, {"apple_oauth_callback_url": "http://example.com/callback"}]
)
def test_apple_requires_https_and_secure_cookies(settings):
    values = {
        "apple_oauth_client_id": "apple-client",
        "apple_oauth_client_secret": SecretStr("secret"),
        "apple_oauth_callback_url": "https://example.com/callback",
        "auth_cookie_secure": True,
        **settings,
    }
    app = create_app(settings=_settings(**values), enforce_auth=True)
    with TestClient(app) as client:
        assert client.get("/auth/config").json()["apple_enabled"] is False
        assert client.get("/auth/apple", follow_redirects=False).status_code == 503


def test_microsoft_tenant_configuration_rejects_invalid_url_components():
    with pytest.raises(ValueError):
        AgentSettings(_env_file=None, microsoft_oauth_tenant="common/../attacker")


@pytest.mark.parametrize("kind", ["tenant", "key-issuer", "consumer-restriction", "organization-restriction"])
def test_microsoft_rejects_invalid_or_unapproved_tenants(signing_keys, kind):
    from tianzhou_agent_platform.auth.oidc import OidcAuthService

    private, key = signing_keys
    consumer = "9188040d-6c67-4c5b-b112-36a304b66dad"
    tenant = consumer if kind == "organization-restriction" else TENANT
    settings = _settings(microsoft_oauth_tenant="consumers" if kind == "consumer-restriction" else "organizations")
    service = OidcAuthService(settings=settings, repository=InMemoryRepository())
    claims = {
        "iss": f"https://login.microsoftonline.com/{tenant}/v2.0",
        "tid": "invalid" if kind == "tenant" else tenant,
        "aud": "client",
        "sub": "subject",
        "iat": int(time.time()),
        "exp": int(time.time()) + 600,
        "nonce": "nonce",
    }
    key = {
        **key,
        "issuer": (
            "https://attacker.example" if kind == "key-issuer" else "https://login.microsoftonline.com/{tenantid}/v2.0"
        ),
    }
    token = jwt.encode(claims, private, algorithm="RS256", headers={"kid": "test-key"})
    with pytest.raises(ValueError):
        service._verify_id_token("microsoft", token, {"keys": [key]}, "client", "nonce", None)
    asyncio.run(service.aclose())
