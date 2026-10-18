"""``aina dev`` (design §12): the real host in process, serving a developer page for one package folder."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.test_aina_scaffold import MCP_URL, _init, _server
from tianzhou_agent_platform.aina.package.devhost import create_dev_app


@pytest.fixture
def dev(tmp_path: Path) -> Any:
    project = tmp_path / "notes"
    _init(project)
    app = create_dev_app(project / "package", port=8790)
    app.state.aina_packages.mcp.servers[MCP_URL] = _server(project).mcp  # the developer's server, in process
    with TestClient(app) as client:
        yield client, project / "package"


def _log(client: TestClient) -> list[dict[str, Any]]:
    return list(client.get("/dev/log").json())


def test_the_folder_is_registered_and_installed_with_every_scope(dev: Any) -> None:
    client, _folder = dev
    state = client.get("/dev/state").json()
    page = client.get("/")

    assert state["errors"] == []
    assert state["record"]["manifest"]["aina"]["id"] == "com.acme.notes"
    assert state["installation"]["granted_permissions"] == ["storage"]
    assert page.status_code == 200 and "/dev/static/dev.js" in page.text
    assert any(entry["kind"] == "mcp" and entry["summary"] == "tools/list" for entry in _log(client))


def test_agent_calls_run_like_the_agent_and_every_request_is_logged(dev: Any) -> None:
    client, _folder = dev
    client.get("/dev/state")

    added = client.post("/dev/agent-call", json={"tool": "add_note", "arguments": {"text": "Ship aina dev"}})
    listed = client.post("/dev/agent-call", json={"tool": "list_notes", "arguments": {}})
    invalid = client.post("/dev/agent-call", json={"tool": "add_note", "arguments": {}})
    log = _log(client)

    assert added.status_code == 200, added.text
    assert listed.json()["visible"]["structuredContent"]["items"][0]["text"] == "Ship aina dev"
    assert listed.json()["widget"]["widget_id"] == "notes"  # the declared result widget
    assert invalid.status_code == 400  # arguments are checked against the reviewed schema
    calls = [entry for entry in log if entry["summary"] == "tools/call add_note"]
    assert calls[0]["request"]["arguments"] == {"text": "Ship aina dev"}
    assert calls[0]["request"]["context"]["token"] == "…"  # the credential is never shown
    assert {entry["summary"].split("?")[0] for entry in log if entry["kind"] == "host-api"} >= {
        "GET /storage/kv/notes",
        "PUT /storage/kv/notes",
    }


def test_edits_reregister_the_package_and_errors_keep_the_last_good_one(dev: Any) -> None:
    client, folder = dev
    before = client.get("/dev/state").json()["record"]["manifest"]["runtime"]["archive_sha256"]
    index = folder / "canvas" / "index.html"
    index.write_text(index.read_text().replace('<h1 id="title">Notes</h1>', '<h1 id="title">Edited</h1>'))
    edited = client.get("/dev/state").json()
    canvas = client.get("/aina-ui/com.acme.notes/0.1.0/canvas/index.html")

    manifest = json.loads((folder / "aina.json").read_text())
    del manifest["aina"]["name"]
    (folder / "aina.json").write_text(json.dumps(manifest))
    broken = client.get("/dev/state").json()

    assert edited["record"]["manifest"]["runtime"]["archive_sha256"] != before
    assert "Edited" in canvas.text and canvas.headers["cache-control"] == "no-store"
    assert any("'name' is a required property" in error for error in broken["errors"])
    assert (
        broken["record"]["manifest"]["runtime"]["archive_sha256"]
        == edited["record"]["manifest"]["runtime"]["archive_sha256"]
    )


def test_settings_and_optional_scopes_are_reinstalled(dev: Any) -> None:
    client, folder = dev
    manifest = json.loads((folder / "aina.json").read_text())
    manifest["permissions"].append({"scope": "llm", "reason": "Summarize notes.", "optional": True})
    (folder / "aina.json").write_text(json.dumps(manifest))
    assert client.get("/dev/state").json()["installation"]["granted_permissions"] == ["llm", "storage"]

    installed = client.post("/dev/install", json={"configuration": {}, "granted_permissions": ["storage"]})

    assert installed.json()["granted_permissions"] == ["storage"]


def test_a_server_that_is_not_running_is_reported(tmp_path: Path) -> None:
    project = tmp_path / "notes"
    _init(project, "--mcp-url", "http://127.0.0.1:9/mcp")
    with TestClient(create_dev_app(project / "package")) as client:
        state = client.get("/dev/state").json()
        call = client.post("/dev/agent-call", json={"tool": "list_notes", "arguments": {}})

    assert state["record"] is None and state["errors"]
    assert call.status_code == 409
