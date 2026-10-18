"""AINA Protocol 2.0 phase P2: context token, Host API scopes and isolation, configuration secrets (design §6.4, §8, §10)."""

from __future__ import annotations

import functools
import json
from typing import Any, Callable

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt  # type: ignore[import-untyped]

from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool
from tests.support.sqlite_store import sqlite_session_factory
from tests.test_aina_package import (
    ENTRY,
    LIST,
    MCP_URL,
    _example_files,
    _example_server,
    _register,
    _set,
    _settings,
    _with_manifest,
)
from tianzhou_agent_platform.aina.package.context import unibot_context
from tianzhou_agent_platform.aina.package.bundled import is_bundled_url
from tianzhou_agent_platform.aina.package.mcp_client import McpCallResult, McpServerInfo, McpToolClient
from tianzhou_agent_platform.aina.package.secrets import aina_key_material
from tianzhou_agent_platform.aina.package.service import AinaPackageService, package_runtime
from tianzhou_agent_platform.aina.package.storage import MAX_INSTALLATION_BYTES, storage_item
from tianzhou_agent_platform.aina.package.tokens import ContextTokenSigner
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.memory_repository import installation_record_id
from tianzhou_agent_platform.store.repository import PersistentRepository

AINA_ID = "com.example.expenses"
SECRET = "sk-live-0123456789abcdef"
KV = "/aina-host/v1/storage/kv"


class RecordingMcp(McpToolClient):
    """Records the headers and ``_meta`` the host sends to MCP servers."""

    def __init__(self, servers: dict[str, Any]) -> None:
        super().__init__(servers=servers)
        self.list_headers: list[dict[str, str]] = []
        self.call_headers: list[dict[str, str]] = []
        self.metas: list[dict[str, Any]] = []

    async def list_tools(self, url: str, *, headers: Any = None, timeout: float = 30.0) -> McpServerInfo:
        if not is_bundled_url(url):  # bundled first-party servers are registered at every startup
            self.list_headers.append(dict(headers or {}))
        return await super().list_tools(url, headers=headers, timeout=timeout)

    async def call_tool(
        self,
        url: str,
        name: str,
        arguments: dict[str, Any],
        *,
        meta: dict[str, Any],
        headers: Any = None,
        timeout: float = 30.0,
    ) -> McpCallResult:
        self.call_headers.append(dict(headers or {}))
        self.metas.append(meta)
        return await super().call_tool(url, name, arguments, meta=meta, headers=headers, timeout=timeout)


def _call(client: TestClient, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return client.portal.call(functools.partial(function, *args, **kwargs))  # type: ignore[union-attr]


def _service(app: FastAPI) -> AinaPackageService:
    return app.state.aina_packages  # type: ignore[no-any-return]


def _token(app: FastAPI, installation: AinaInstallation, *, invocation_id: str = "call_test") -> str:
    """The context token a tool call for ``installation`` would carry."""
    service = _service(app)
    record = app.state.repository._ainas[installation.aina_id]  # noqa: SLF001
    context = unibot_context(
        secret=aina_key_material(service.settings),
        manifest=package_runtime(record).package,
        installation=installation,
        invocation_id=invocation_id,
        source="agent",
        conversation_id=None,
        workspace_id=None,
    )
    return service.mint_token(installation, context)


def _installation(aina_id: str = AINA_ID, *, user_id: str = "alice", tenant_id: str = "t1", scopes: list[str] | None = None) -> AinaInstallation:
    return AinaInstallation(
        user_id=user_id,
        tenant_id=tenant_id,
        aina_id=aina_id,
        installed_version="1.2.0",
        granted_permissions=["storage"] if scopes is None else scopes,
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def host() -> Any:
    server = _example_server()
    mcp = RecordingMcp({MCP_URL: server.mcp})
    app = create_app(settings=_settings(), llm=ScriptedLLM([]), mcp_client=mcp)
    with TestClient(app) as client:
        assert _register(client, _example_files()).status_code == 201
        yield client, app


# --- Context token and JWKS -------------------------------------------------------------------------------------


def test_tool_call_carries_a_context_token_an_mcp_server_can_verify_with_the_jwks() -> None:
    server = _example_server()
    mcp = RecordingMcp({MCP_URL: server.mcp})
    llm = ScriptedLLM(
        [call_first_tool(prefix=ENTRY, call_id="a"), call_first_tool(prefix=LIST, call_id="l"), assistant("None.")]
    )
    app = create_app(settings=_settings(), llm=llm, mcp_client=mcp)
    server.HOST_TRANSPORT = httpx.ASGITransport(app=app)
    with TestClient(app) as client:
        _register(client, _example_files())
        client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []})
        client.post("/chat", json={"message": "List my expenses"})
        jwks = client.get("/.well-known/aina-host-jwks.json").json()

    context = mcp.metas[-1]["unibot/context"]
    assert context["host"] == {"api_base": "http://testserver/aina-host/v1"}
    claims = jwt.decode(
        context["token"],
        jwks["keys"][0],
        algorithms=["ES256"],
        audience=AINA_ID,
        issuer="http://testserver/aina-host/v1",
    )
    # The token carries exactly the pairwise identity of _meta, never the host's raw ids.
    assert claims["sub"] == context["user"]["id"]
    assert claims["tenant"] == context["tenant_id"]
    assert claims["installation_id"] == context["installation_id"]
    assert claims["scopes"] == ["storage"]  # low-risk scopes are granted without asking
    assert claims["exp"] - claims["iat"] <= 600
    assert claims["invocation_id"] == "l"
    raw = json.dumps(claims)
    assert "anonymous" not in raw and "user_dev" not in raw and "default" not in raw


def test_signing_key_is_shared_by_nodes_with_the_same_secret() -> None:
    first = ContextTokenSigner.from_settings(_settings())
    second = ContextTokenSigner.from_settings(_settings())
    other = ContextTokenSigner.from_settings(_settings().model_copy(update={"aina_secret_key": None, "auth_secret": _secret("x" * 40)}))

    assert first.jwks() == second.jwks()
    assert first.kid != other.kid


def _secret(value: str) -> Any:
    from pydantic import SecretStr

    return SecretStr(value)


def test_host_api_rejects_missing_tampered_expired_and_foreign_tokens(host: Any) -> None:
    client, app = host
    installation = _installation()
    _call(client, app.state.repository.put_installation, installation)
    token = _token(app, installation)
    service = _service(app)
    expired = service.tokens.mint(
        reference=service.tokens.verify(token).reference,
        claims={"scopes": ["storage"]},
        ttl_seconds=-1,
    )
    foreign_signer = ContextTokenSigner.from_settings(
        _settings().model_copy(update={"aina_secret_key": _secret("another-deployment-secret-0123456789")})
    )
    foreign = foreign_signer.mint(reference=service.tokens.verify(token).reference, claims={"scopes": ["storage"]})

    assert client.get(KV).status_code == 401
    assert client.get(KV, headers={"Authorization": f"Basic {token}"}).status_code == 401
    assert client.get(KV, headers=_bearer(token[:-4] + "AAAA")).status_code == 401
    assert client.get(KV, headers=_bearer(expired)).status_code == 401
    assert client.get(KV, headers=_bearer(foreign)).status_code == 401
    assert client.get(KV, headers=_bearer(token)).status_code == 200


# --- Storage, scopes and isolation -----------------------------------------------------------------------------


def test_storage_round_trip_and_quota(host: Any) -> None:
    client, app = host
    installation = _installation()
    _call(client, app.state.repository.put_installation, installation)
    headers = _bearer(_token(app, installation))

    put = client.put(f"{KV}/notes/2026-09", json={"value": {"total": 42.5}}, headers=headers)
    client.put(f"{KV}/notes/2026-10", json={"value": [1, 2]}, headers=headers)
    client.put(f"{KV}/other", json={"value": "x"}, headers=headers)
    got = client.get(f"{KV}/notes/2026-09", headers=headers)
    listed = client.get(KV, params={"prefix": "notes/"}, headers=headers)
    deleted = client.delete(f"{KV}/notes/2026-09", headers=headers)
    missing = client.get(f"{KV}/notes/2026-09", headers=headers)
    too_big = client.put(f"{KV}/big", json={"value": "x" * (300 * 1024)}, headers=headers)

    assert put.status_code == 200 and put.json()["value"] == {"total": 42.5}
    assert got.json()["value"] == {"total": 42.5}
    assert [item["key"] for item in listed.json()["items"]] == ["notes/2026-09", "notes/2026-10"]
    assert deleted.status_code == 204
    assert missing.status_code == 404
    assert too_big.status_code == 413
    key = installation_record_id("t1", "alice", AINA_ID)
    for index in range(MAX_INSTALLATION_BYTES // (250 * 1024)):
        _call(client, app.state.repository.put_aina_storage_item, storage_item(
            installation_key=key, aina_id=AINA_ID, key=f"fill/{index}", value="x" * (250 * 1024)
        ))
    over = client.put(f"{KV}/one-more", json={"value": "x" * (250 * 1024)}, headers=headers)
    assert over.status_code == 507
    assert over.json()["error"]["code"] == "QUOTA_EXCEEDED"


def test_storage_requires_a_granted_scope_and_revocation_applies_to_live_tokens(host: Any) -> None:
    client, app = host
    without = _installation(user_id="bob", scopes=[])
    granted = _installation(user_id="carol")
    _call(client, app.state.repository.put_installation, without)
    _call(client, app.state.repository.put_installation, granted)
    carol = _bearer(_token(app, granted))

    denied = client.get(KV, headers=_bearer(_token(app, without)))
    before = client.get(KV, headers=carol)
    _call(client, app.state.repository.put_installation, granted.model_copy(update={"granted_permissions": []}))
    revoked = client.get(KV, headers=carol)
    _call(client, app.state.repository.remove_installation, tenant_id="t1", user_id="carol", aina_id=AINA_ID)
    uninstalled = client.get(KV, headers=carol)

    assert denied.status_code == 403 and denied.json()["error"]["code"] == "PERMISSION_DENIED"
    assert before.status_code == 200
    assert revoked.status_code == 403  # the token still says "storage", the installation no longer does
    assert uninstalled.status_code == 401


def test_storage_is_isolated_across_users_tenants_and_ainas(host: Any) -> None:
    client, app = host
    second_package = _with_manifest(_example_files(), _set("aina.id", "com.example.budget"))
    assert _register(client, second_package).status_code == 201
    alice = _installation(user_id="alice", tenant_id="t1")
    alice_other_tenant = _installation(user_id="alice", tenant_id="t2")
    bob = _installation(user_id="bob", tenant_id="t1")
    alice_budget = _installation("com.example.budget", user_id="alice", tenant_id="t1")
    for item in (alice, alice_other_tenant, bob, alice_budget):
        _call(client, app.state.repository.put_installation, item)

    client.put(f"{KV}/secret-plan", json={"value": "alice's"}, headers=_bearer(_token(app, alice)))

    for other in (alice_other_tenant, bob, alice_budget):
        headers = _bearer(_token(app, other))
        assert client.get(f"{KV}/secret-plan", headers=headers).status_code == 404
        assert client.get(KV, headers=headers).json()["items"] == []
    assert client.get(f"{KV}/secret-plan", headers=_bearer(_token(app, alice))).json()["value"] == "alice's"
    # Pairwise ids: the same person looks different to each AINA and in each tenant.
    subjects = {
        jwt.get_unverified_claims(_token(app, item))["sub"] for item in (alice, alice_other_tenant, bob, alice_budget)
    }
    assert len(subjects) == 4


def test_uninstall_deletes_private_storage_unless_kept() -> None:
    server = _example_server()
    app = create_app(settings=_settings(), llm=ScriptedLLM([]), mcp_client=McpToolClient(servers={MCP_URL: server.mcp}))
    with TestClient(app) as client:
        _register(client, _example_files())
        installed = AinaInstallation.model_validate(
            {
                key: value
                for key, value in client.post(f"/ainas/{AINA_ID}/install", json={}).json().items()
                if key != "configured_secrets"
            }
        )
        client.put(f"{KV}/kept", json={"value": 1}, headers=_bearer(_token(app, installed)))
        client.delete(f"/ainas/{AINA_ID}/install", params={"keep_data": "true"})
        kept = _call(client, app.state.repository.list_aina_storage_items, _key(installed), prefix="", limit=10)
        client.post(f"/ainas/{AINA_ID}/install", json={})
        client.delete(f"/ainas/{AINA_ID}/install")
        removed = _call(client, app.state.repository.list_aina_storage_items, _key(installed), prefix="", limit=10)

    assert [item.key for item in kept] == ["kept"]
    assert removed == []


def _key(installation: AinaInstallation) -> str:
    return installation_record_id(installation.tenant_id, installation.user_id, installation.aina_id)


# --- Configuration, secrets and MCP auth ------------------------------------------------------------------------


def _bearer_package() -> dict[str, bytes]:
    def change(manifest: dict[str, Any]) -> None:
        manifest["tools"]["mcp"]["auth"] = {"type": "bearer", "secret": "api_token"}
        manifest["config"]["schema"]["properties"]["api_token"] = {
            "type": "string",
            "minLength": 8,
            "title": "Expenses API token",
            "x-secret": True,
        }
        manifest["config"]["schema"]["required"] = ["api_token"]

    return _with_manifest(_example_files(), change)


def test_secret_config_is_sealed_used_only_for_mcp_auth_and_never_returned() -> None:
    server = _example_server()
    mcp = RecordingMcp({MCP_URL: server.mcp})
    llm = ScriptedLLM(
        [call_first_tool(prefix=ENTRY, call_id="a"), call_first_tool(prefix=LIST, call_id="l"), assistant("None.")]
    )
    app = create_app(settings=_settings(), llm=llm, mcp_client=mcp)
    server.HOST_TRANSPORT = httpx.ASGITransport(app=app)
    with TestClient(app) as client:
        archive_files = _bearer_package()
        from tianzhou_agent_platform.aina.project import build_project_archive

        registered = client.post(
            "/ainas/packages",
            files={"file": ("e.aina.zip", build_project_archive(archive_files), "application/zip")},
            data={"credential": "registration-token"},
        )
        missing = client.post(f"/ainas/{AINA_ID}/install", json={"configuration": {"currency": "EUR"}})
        invalid = client.post(f"/ainas/{AINA_ID}/install", json={"configuration": {"api_token": "short"}})
        installed = client.post(
            f"/ainas/{AINA_ID}/install",
            json={"configuration": {"currency": "EUR", "api_token": SECRET}},
        )
        # Reinstalling without the secret keeps the stored one.
        reinstalled = client.post(f"/ainas/{AINA_ID}/install", json={"configuration": {"currency": "USD"}})
        listed = client.get("/installations")
        chat = client.post("/chat", json={"message": "List my expenses"}).json()
        trace = client.get(f"/traces/{chat['trace_id']}")
        stored = app.state.repository._installations  # noqa: SLF001

    assert registered.status_code == 201, registered.text
    assert mcp.list_headers[0] == {"Authorization": "Bearer registration-token"}
    assert missing.status_code == 422 and "'api_token' is a required property" in missing.json()["error"]["message"]
    assert invalid.status_code == 422 and "short" not in invalid.json()["error"]["message"]
    assert installed.status_code == 200, installed.text
    assert installed.json()["configuration"] == {"currency": "EUR"}
    assert installed.json()["configured_secrets"] == ["api_token"]
    assert reinstalled.json()["configured_secrets"] == ["api_token"]
    (record,) = stored.values()
    assert record.secrets["api_token"].startswith("v1:") and SECRET not in record.secrets["api_token"]
    # The secret authenticates the MCP connection (install-time check and the tool call), nothing else.
    assert mcp.list_headers[-1] == {"Authorization": f"Bearer {SECRET}"}
    assert mcp.call_headers[-1] == {"Authorization": f"Bearer {SECRET}"}
    assert mcp.metas[-1]["unibot/context"]["config"] == {"currency": "USD"}
    for text in (listed.text, trace.text, json.dumps(mcp.metas[-1])):
        assert SECRET not in text


def test_sealed_secret_does_not_open_for_another_installation() -> None:
    from tianzhou_agent_platform.aina.package.configuration import secret_context
    from tianzhou_agent_platform.aina.package.secrets import SecretBox

    box = SecretBox("k" * 40)
    sealed = box.seal(SECRET, context=secret_context(tenant_id="t1", user_id="alice", aina_id=AINA_ID))

    assert box.open(sealed, context=secret_context(tenant_id="t1", user_id="alice", aina_id=AINA_ID)) == SECRET
    with pytest.raises(PlatformError):
        box.open(sealed, context=secret_context(tenant_id="t1", user_id="bob", aina_id=AINA_ID))
    with pytest.raises(PlatformError):
        SecretBox("z" * 40).open(sealed, context=secret_context(tenant_id="t1", user_id="alice", aina_id=AINA_ID))


# --- SQL persistence ----------------------------------------------------------------------------------------------


class _SqlOnly:
    def __init__(self) -> None:
        self.session_factory = sqlite_session_factory()


async def test_sql_repository_persists_storage_and_hidden_secrets() -> None:
    stores = StorageStores(mysql=_SqlOnly(), redis=None, nas=None)  # type: ignore[arg-type]
    first, second = PersistentRepository(stores), PersistentRepository(stores)
    installation = _installation().model_copy(update={"secrets": {"api_token": "v1:sealed"}})
    await first.put_installation(installation)
    key = _key(installation)
    await first.put_aina_storage_item(storage_item(installation_key=key, aina_id=AINA_ID, key="a/1", value={"n": 1}))
    await first.put_aina_storage_item(storage_item(installation_key=key, aina_id=AINA_ID, key="a/1", value={"n": 2}))
    await first.put_aina_storage_item(storage_item(installation_key=key, aina_id=AINA_ID, key="b", value=[1]))

    loaded = await second.get_installation(tenant_id="t1", user_id="alice", aina_id=AINA_ID)
    assert loaded.secrets == {"api_token": "v1:sealed"}
    assert "secrets" not in loaded.model_dump() and loaded.model_dump()["configured_secrets"] == ["api_token"]
    assert [(item.key, item.value) for item in await second.list_aina_storage_items(key, prefix="a/", limit=10)] == [
        ("a/1", {"n": 2})
    ]
    assert (await second.get_aina_storage_item(key, "b")).value == [1]  # type: ignore[union-attr]
    assert await second.delete_aina_storage_item(key, "b") is True
    assert await second.delete_aina_storage(aina_id=AINA_ID) == 1
    assert await second.list_aina_storage_items(key, prefix="", limit=10) == []
