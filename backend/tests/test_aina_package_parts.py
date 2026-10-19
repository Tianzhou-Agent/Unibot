"""An app's parts are optional (tools, skills or a canvas, at least one), and its tools may come from several servers."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool
from tests.test_aina_package import ENTRY, MCP_URL, _example_files, _example_server, _register, _settings
from tianzhou_agent_platform.aina.package.mcp_client import McpCallResult, McpToolClient
from tianzhou_agent_platform.aina.package.service import consent_changes, package_runtime
from tianzhou_agent_platform.aina.package.validation import validate_package_files
from tianzhou_agent_platform.main import create_app

AINA_ID = "com.example.expenses"
SECOND_URL = "http://127.0.0.1:8766/mcp"


class RoutingMcp(McpToolClient):
    """Records which server each tool call went to."""

    def __init__(self, servers: dict[str, Any]) -> None:
        super().__init__(servers=servers)
        self.calls: list[tuple[str, str]] = []

    async def call_tool(self, url: str, name: str, arguments: dict[str, Any], **kwargs: Any) -> McpCallResult:
        self.calls.append((url, name))
        return await super().call_tool(url, name, arguments, **kwargs)


def _files(change: Any) -> dict[str, bytes]:
    files = _example_files()
    manifest = json.loads(files["aina.json"])
    change(manifest)
    return {**files, "aina.json": json.dumps(manifest).encode()}


def _two_servers(manifest: dict[str, Any]) -> None:
    manifest["tools"]["mcp"] = [
        {"id": "ledger", "url": MCP_URL, "auth": {"type": "none"}},
        {"id": "bank", "url": SECOND_URL, "auth": {"type": "none"}},
    ]
    for name, tool in manifest["tools"]["expose"].items():
        tool["server"] = "bank" if name == "list_expenses" else "ledger"


def _without(*parts: str) -> Any:
    def change(manifest: dict[str, Any]) -> None:
        for part in parts:
            manifest.pop(part, None)
        if "tools" in parts:
            manifest.pop("widgets", None)  # the example's widgets belong to its tools
            for skill in manifest.get("skills", []):
                skill["tools"] = []

    return change


def test_tools_come_from_the_server_that_lists_them() -> None:
    ledger, bank = _example_server(), _example_server()
    mcp = RoutingMcp({MCP_URL: ledger.mcp, SECOND_URL: bank.mcp})
    app = create_app(settings=_settings(), llm=ScriptedLLM([]), mcp_client=mcp)
    bank.HOST_TRANSPORT = httpx.ASGITransport(app=app)
    with TestClient(app) as client:
        registered = _register(client, _files(_two_servers))
        client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": ["storage"]})
        listed = client.post(f"/ainas/{AINA_ID}/app-tools/list_expenses", json={"arguments": {}})
        refreshed = client.post(f"/ainas/{AINA_ID}/tools/refresh")

    assert registered.status_code == 201, registered.text
    assert listed.status_code == 200, listed.text
    assert mcp.calls == [(SECOND_URL, "list_expenses")]
    assert refreshed.status_code == 200 and refreshed.json()["last_health"]["status"] == "healthy"


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (
            lambda m: (_two_servers(m), m["tools"]["expose"]["list_expenses"].pop("server")),
            "list_expenses.server: name the server",
        ),
        (
            lambda m: (_two_servers(m), m["tools"]["expose"]["list_expenses"].update(server="nope")),
            "no MCP server has the id 'nope'",
        ),
        (lambda m: (_two_servers(m), m["tools"]["mcp"][1].update(id="ledger")), "server id 'ledger' is duplicated"),
        (
            lambda m: (_two_servers(m), [server.update(auth={"type": "oauth2"}) for server in m["tools"]["mcp"]]),
            "at most one MCP server of an app may use OAuth",
        ),
        (
            lambda m: m["tools"].update(mcp=[{"url": MCP_URL, "auth": {"type": "none"}}]),
            "tools/mcp/0: 'id' is a required",
        ),
        (
            _without("tools", "skills", "canvas"),
            "an app needs at least one of tools (an MCP server), skills or a canvas",
        ),
    ],
)
def test_server_rules(change: Any, expected: str) -> None:
    report = validate_package_files(_files(change))

    assert not report.valid
    assert any(expected in error for error in report.errors), report.errors


def test_an_app_of_only_skills_is_registered_installed_and_activated() -> None:
    llm = ScriptedLLM(
        [call_first_tool(prefix=ENTRY, call_id="call_activate"), assistant("Here is how to close a month.")]
    )
    app = create_app(settings=_settings(), llm=llm, mcp_client=McpToolClient(servers={}))
    with TestClient(app) as client:
        registered = _register(client, _files(_without("tools", "canvas")))
        installed = client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": ["storage"]})
        reply = client.post("/chat", json={"message": "How do I close the month?"}).json()
        checked = client.app.state.aina_packages  # type: ignore[attr-defined]
        outcomes = client.portal.call(checked.check_all_tools)  # type: ignore[union-attr]
        messages = client.get(f"/conversations/{reply['conversation_id']}").json()["messages"]

    assert registered.status_code == 201, registered.text
    assert registered.json()["manifest"]["runtime"]["tools"] == []
    assert installed.status_code == 200, installed.text
    assert reply["status"] == "completed", reply
    activation = next(message["content"] for message in messages if message["role"] == "tool")
    assert f"{AINA_ID}/load_skill" in activation
    assert AINA_ID not in outcomes  # nothing to check without a server


def test_an_app_of_only_a_canvas_is_valid() -> None:
    def canvas_only(manifest: dict[str, Any]) -> None:
        _without("tools", "skills")(manifest)
        manifest["canvas"].pop("tools")

    assert validate_package_files(_files(canvas_only)).valid


def test_a_new_server_needs_the_users_consent_again() -> None:
    app = create_app(
        settings=_settings(),
        llm=ScriptedLLM([]),
        mcp_client=McpToolClient(servers={MCP_URL: _example_server().mcp, SECOND_URL: _example_server().mcp}),
    )
    with TestClient(app) as client:
        _register(client, _example_files())
        before = package_runtime(client.get(f"/ainas/{AINA_ID}").json() and _record(client))

        def upgraded(manifest: dict[str, Any]) -> None:
            _two_servers(manifest)
            manifest["aina"]["version"] = "1.3.0"

        _register(client, _files(upgraded))
        after = package_runtime(_record(client))

    assert f"Tools from a new MCP server at {SECOND_URL}" in consent_changes(before, after)


def _record(client: TestClient) -> Any:
    from tianzhou_agent_platform.aina.protocol.models import AinaRecord

    return AinaRecord.model_validate(client.get(f"/ainas/{AINA_ID}").json())
