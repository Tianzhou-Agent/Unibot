"""AINA Protocol 2.0 upgrades (design §10): a version that widens what an AINA can do needs the user's consent again."""

from __future__ import annotations

from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from tests.support.fake_llm import LLMResult, ScriptedLLM, assistant
from tests.test_aina_host_api import AINA_ID, RecordingMcp, _call, _token
from tests.test_aina_package import ENTRY, MCP_URL, _example_files, _example_server, _register, _set, _settings, _with_manifest
from tianzhou_agent_platform.main import create_app


def _version(version: str, *changes: Callable[[dict[str, Any]], None]) -> dict[str, bytes]:
    files = _with_manifest(_example_files(), _set("aina.version", version))
    for change in changes:
        files = _with_manifest(files, change)
    return files


def _client(llm: ScriptedLLM | None = None) -> TestClient:
    server = _example_server()
    client = TestClient(create_app(settings=_settings(), llm=llm or ScriptedLLM([]), mcp_client=RecordingMcp({MCP_URL: server.mcp})))
    client.__enter__()
    return client


def _installation(client: TestClient) -> dict[str, Any]:
    (installation,) = client.get("/installations").json()
    return installation


def _without_report(manifest: dict[str, Any]) -> None:
    del manifest["tools"]["expose"]["monthly_report"]


@pytest.mark.parametrize(
    ("v1", "v2", "expected"),
    [
        ((_without_report,), (), "New tool 'monthly_report' (read)"),
        ((_set("tools.expose.add_expense.effect", "destructive"),), (), "Tool 'add_expense' now asks less often (destructive → write)"),
        ((), (_set("tools.expose.sync_bank.visibility", ["model", "app"]),), "The agent can now call 'sync_bank'"),
        ((), (_set("permissions.1.optional", False),), "Needs the 'documents.read' permission"),
    ],
)
def test_an_upgrade_that_widens_the_aina_pauses_installations(
    v1: tuple[Callable[[dict[str, Any]], None], ...],
    v2: tuple[Callable[[dict[str, Any]], None], ...],
    expected: str,
) -> None:
    client = _client()
    try:
        assert _register(client, _version("1.0.0", *v1)).status_code == 201
        client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []})
        assert _register(client, _version("2.0.0", *v2)).status_code == 201
        paused = _installation(client)
    finally:
        client.__exit__(None, None, None)

    assert paused["status"] == "consent_required"
    assert expected in paused["consent_changes"]


def test_an_upgrade_that_widens_nothing_moves_installations_silently() -> None:
    client = _client()
    try:
        _register(client, _version("1.0.0"))
        client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": ["documents.read"]})
        # Stricter effects and a newly required scope the user already granted widen nothing.
        _register(
            client,
            _version("1.1.0", _set("tools.expose.list_expenses.effect", "write"), _set("permissions.1.optional", False)),
        )
        installation = _installation(client)
    finally:
        client.__exit__(None, None, None)

    assert installation["status"] == "active"
    assert installation["installed_version"] == "1.1.0"
    assert installation["consent_changes"] == []


def test_a_paused_installation_is_unusable_until_the_user_approves_again() -> None:
    def no_entry(*, tools: list[dict[str, Any]], **_: Any) -> LLMResult:
        assert not any(item["function"]["name"].startswith(ENTRY) for item in tools)
        return assistant("Expenses is paused.")

    def entry_back(*, tools: list[dict[str, Any]], **_: Any) -> LLMResult:
        assert any(item["function"]["name"].startswith(ENTRY) for item in tools)
        return assistant("Expenses is back.")

    client = _client(ScriptedLLM([no_entry, entry_back]))
    app = client.app
    try:
        _register(client, _version("1.0.0", _without_report))
        client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": [], "configuration": {"currency": "EUR"}})
        (before,) = _call(client, app.state.repository.list_installations)  # type: ignore[attr-defined]
        token = _token(app, before)
        _register(client, _version("2.0.0"))

        chat_paused = client.post("/chat", json={"message": "Add 3 for food"})
        host_api = client.get("/aina-host/v1/storage/kv", headers={"Authorization": f"Bearer {token}"})
        app_tool = client.post(f"/ainas/{AINA_ID}/app-tools/list_expenses", json={"arguments": {}})
        approved = client.post(
            f"/ainas/{AINA_ID}/install", json={"granted_permissions": [], "configuration": {"currency": "EUR"}}
        )
        chat_back = client.post("/chat", json={"message": "Add 3 for food"})
    finally:
        client.__exit__(None, None, None)

    assert chat_paused.json()["status"] == "completed"  # the paused AINA was simply not offered
    assert host_api.status_code == 401
    assert app_tool.status_code == 403
    assert approved.json()["status"] == "active"
    assert approved.json()["installed_version"] == "2.0.0" and approved.json()["consent_changes"] == []
    assert chat_back.json()["status"] == "completed"
