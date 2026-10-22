"""AINA Protocol 2.0 phase P3a: packaged UI files, the canvas widget, app tool calls and identity tokens (design §9)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from jose import jwt  # type: ignore[import-untyped]

from tests.support.fake_llm import ScriptedLLM
from tests.test_aina_host_api import AINA_ID, RecordingMcp
from tests.test_aina_package import (
    MCP_URL,
    REPO,
    _example_files,
    _example_server,
    _register,
    _set,
    _settings,
    _with_manifest,
)
from tianzhou_agent_platform.main import create_app

UI = f"/aina-ui/{AINA_ID}/1.2.0"
SDK = REPO / "spec" / "aina" / "2.0" / "ui"


def _client(files: dict[str, bytes] | None = None, *, install: bool = True) -> tuple[TestClient, RecordingMcp]:
    server = _example_server()
    mcp = RecordingMcp({MCP_URL: server.mcp})
    app = create_app(settings=_settings(), llm=ScriptedLLM([]), mcp_client=mcp)
    server.HOST_TRANSPORT = httpx.ASGITransport(app=app)
    client = TestClient(app)
    client.__enter__()
    assert _register(client, files or _example_files()).status_code == 201
    if install:
        assert client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []}).status_code == 200
    return client, mcp


@pytest.fixture
def ui() -> Any:
    client, mcp = _client()
    yield client, mcp
    client.__exit__(None, None, None)


# --- Packaged UI files -------------------------------------------------------------------------------------------


def test_ui_files_are_served_sandboxed_without_network_access(ui: Any) -> None:
    client, _mcp = ui
    page = client.get(f"{UI}/canvas/index.html")
    script = client.get(f"{UI}/canvas/app.js")

    assert page.status_code == 200 and page.headers["content-type"] == "text/html; charset=utf-8"
    assert script.headers["content-type"] == "text/javascript; charset=utf-8"
    policy = page.headers["content-security-policy"]
    # Opened directly in a tab, the file still runs with an opaque origin, never as Superbot.
    assert policy.startswith("sandbox allow-scripts allow-forms allow-downloads;")
    assert "allow-same-origin" not in policy
    assert "connect-src 'none'" in policy
    assert "frame-ancestors http://127.0.0.1:5173" in policy
    assert page.headers["x-content-type-options"] == "nosniff"
    assert page.headers["access-control-allow-origin"] == "*"  # its own module scripts are cross-origin requests


@pytest.mark.parametrize(
    "path",
    [
        f"{UI}/aina.json",
        f"{UI}/skills/record-expense/SKILL.md",
        f"{UI}/skills/monthly-close/references/categories.md",
        f"{UI}/canvas/missing.html",
        f"/aina-ui/{AINA_ID}/9.9.9/canvas/index.html",
        "/aina-ui/unibot-memory/1.0.0/index.html",
    ],
)
def test_only_ui_files_of_the_registered_version_are_served(ui: Any, path: str) -> None:
    client, _mcp = ui

    assert client.get(path).status_code == 404


def test_vendored_sdk_matches_the_published_one() -> None:
    package = REPO / "examples" / "expenses-aina" / "package"
    for name in ("aina-ui.js", "aina-ui.css"):
        published = (SDK / name).read_bytes()
        assert (package / "canvas" / name).read_bytes() == published
        assert (package / "widgets" / name).read_bytes() == published


# --- Canvas widget ------------------------------------------------------------------------------------------------


def test_opening_the_app_returns_its_canvas_even_without_optional_scopes(ui: Any) -> None:
    client, _mcp = ui
    opened = client.post(f"/ainas/{AINA_ID}/open", json={})

    assert opened.status_code == 200, opened.text
    widget = opened.json()["main_widget"]
    assert widget["kind"] == "aina_canvas"
    assert widget["canvas"] == {"kind": "package", "version": "1.2.0", "entry": "canvas/index.html", "url": None}


# --- App tool calls -----------------------------------------------------------------------------------------------


def _app_call(client: TestClient, tool: str, arguments: dict[str, Any], **extra: Any) -> httpx.Response:
    return client.post(f"/ainas/{AINA_ID}/app-tools/{tool}", json={"arguments": arguments, **extra})


def test_app_tool_calls_follow_visibility_and_need_host_confirmation_to_change_data(ui: Any) -> None:
    client, mcp = ui

    listed = _app_call(client, "list_expenses", {})
    unconfirmed = _app_call(client, "add_expense", {"amount": 4.5, "category": "food"})
    added = _app_call(client, "add_expense", {"amount": 4.5, "category": "food"}, confirmed=True)
    app_only = _app_call(client, "sync_bank", {}, confirmed=True)
    long_running = _app_call(client, "monthly_report", {"month": "2026-10"})
    invalid = _app_call(client, "list_expenses", {"month": 10})
    unknown = _app_call(client, "send_invoice", {}, confirmed=True)

    assert listed.status_code == 200 and listed.json() == {"structuredContent": {"items": [], "total": 0.0}}
    assert unconfirmed.status_code == 428 and unconfirmed.json()["error"]["code"] == "CONFIRMATION_REQUIRED"
    assert added.status_code == 200 and added.json()["structuredContent"]["amount"] == 4.5
    assert app_only.status_code == 200
    assert long_running.status_code == 409
    assert invalid.status_code == 400
    assert unknown.status_code == 403
    # The MCP server is told the call came from the canvas.
    assert {meta["unibot/context"]["source"] for meta in mcp.metas} == {"canvas"}


def test_app_tool_calls_tell_the_mcp_server_which_conversation_the_canvas_is_open_beside(ui: Any) -> None:
    client, mcp = ui
    conversation_id = client.post("/conversations", json={"title": "Expenses"}).json()["id"]

    beside = _app_call(client, "list_expenses", {}, conversation_id=conversation_id)
    alone = _app_call(client, "list_expenses", {})
    unknown = _app_call(client, "list_expenses", {}, conversation_id="conv_missing")

    assert beside.status_code == 200 and alone.status_code == 200
    assert unknown.status_code == 404
    # A pairwise id, never the host's own conversation id.
    first, second = (meta["unibot/context"] for meta in mcp.metas)
    assert first["conversation_id"].startswith("conv_") and first["conversation_id"] != conversation_id
    assert "conversation_id" not in second


def test_model_only_tools_and_uninstalled_users_are_refused() -> None:
    files = _with_manifest(_example_files(), _set("tools.expose.delete_expense.visibility", ["model"]))
    client, _mcp = _client(files)
    try:
        model_only = _app_call(client, "delete_expense", {"expense_id": "e_1"}, confirmed=True)
        client.delete(f"/ainas/{AINA_ID}/install")
        uninstalled = _app_call(client, "list_expenses", {})
    finally:
        client.__exit__(None, None, None)

    assert model_only.status_code == 403
    assert uninstalled.status_code == 403


# --- URL canvas identity token ------------------------------------------------------------------------------------


def test_url_canvas_gets_a_pairwise_identity_token_that_opens_no_host_api() -> None:
    def url_canvas(manifest: dict[str, Any]) -> None:
        del manifest["canvas"]["entry"]  # the canvas tools stay: a URL canvas implements them too
        manifest["canvas"]["url"] = "https://expenses.example.com/unibot/"

    files = _with_manifest(_example_files(), url_canvas)
    client, _mcp = _client(files)
    try:
        opened = client.post(f"/ainas/{AINA_ID}/open", json={}).json()["main_widget"]
        token = client.post(f"/ainas/{AINA_ID}/identity-token").json()["token"]
        jwks = client.get("/.well-known/aina-host-jwks.json").json()
        storage = client.get("/aina-host/v1/storage/kv", headers={"Authorization": f"Bearer {token}"})
    finally:
        client.__exit__(None, None, None)

    assert opened["canvas"] == {"kind": "url", "version": "1.2.0", "entry": None, "url": "https://expenses.example.com/unibot/"}
    claims = jwt.decode(token, jwks["keys"][0], algorithms=["ES256"], audience=AINA_ID, issuer="http://testserver/aina-host/v1")
    assert claims["use"] == "identity" and claims["scopes"] == []
    assert claims["sub"].startswith("pu_")
    assert storage.status_code == 401  # an identity token is not a Host API credential


def test_packaged_canvas_cannot_request_an_identity_token(ui: Any) -> None:
    client, _mcp = ui

    assert client.post(f"/ainas/{AINA_ID}/identity-token").status_code == 403
