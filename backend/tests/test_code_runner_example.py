"""Code Runner is reachable only through an explicitly installed example package."""

import json
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.example_ainas import example_files, example_server
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool
from tests.test_aina_package import _call, _register, _settings
from tianzhou_agent_platform.aina.builtin import ensure_builtin_ainas
from tianzhou_agent_platform.aina.memory.builtin import unibot_memory_record
from tianzhou_agent_platform.aina.package.mcp_client import McpToolClient
from tianzhou_agent_platform.main import create_app

AINA_ID = "unibot.code-runner"
MCP_URL = "http://127.0.0.1:8768/mcp"


@pytest.mark.parametrize("language", ["python", "bash", "node"])
def test_explicit_installation_runs_code_and_uninstall_revokes_it(
    monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    server = example_server("code-runner-aina")
    requests: list[dict[str, Any]] = []

    def execute(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/exec"
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "succeeded", "stdout": "42", "stderr": "", "exit_code": 0})

    monkeypatch.setattr(server, "SANDBOX_TRANSPORT", httpx.MockTransport(execute))
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=f"mcp_unibot_code-runner_run_{language}_", arguments='{"script":"print(42)"}'),
            assistant("42"),
        ]
    )
    app = create_app(settings=_settings(), llm=llm, mcp_client=McpToolClient(servers={MCP_URL: server.mcp}))
    with TestClient(app) as client:
        assert client.get(f"/ainas/{AINA_ID}").status_code == 404
        registered = _register(client, example_files("code-runner-aina"))
        assert registered.status_code == 201, registered.text
        tool_url = f"/ainas/{AINA_ID}/app-tools/run_{language}"
        assert client.post(tool_url, json={"arguments": {"script": "print(42)"}}).status_code == 403
        installed = client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []})
        assert installed.status_code == 200, installed.text
        # A host restart must preserve the user's manually installed package.
        _call(client, ensure_builtin_ainas, app.state.repository)
        assert client.post(f"/ainas/{AINA_ID}/open", json={}).json()["main_widget"]["kind"] == "aina_canvas"
        pending = client.post("/chat", json={"message": "Run this code", "capability": f"aina:{AINA_ID}"})
        assert pending.json()["status"] == "approval_required", pending.text
        assert requests == []
        completed = client.post(f"/approvals/{pending.json()['approval']['id']}/confirm", json={})
        assert completed.json()["status"] == "completed", completed.text
        assert requests == [
            {"language": language, "script": "print(42)", "timeout_seconds": 60, "working_directory": "."}
        ]
        assert client.delete(f"/ainas/{AINA_ID}/install").status_code == 204
        assert client.post(tool_url, json={"arguments": {"script": "print(42)"}}).status_code == 403
        assert client.post(f"/ainas/{AINA_ID}/open", json={}).status_code == 404
        assert len(requests) == 1


def test_default_agent_cannot_execute_legacy_sandbox_tools() -> None:
    def inspect(*, tools: list[dict[str, Any]], **_: Any) -> Any:
        names = [tool["function"]["name"] for tool in tools]
        assert not any("sandbox" in name or "code-runner" in name or "image-recognition" in name for name in names)
        return assistant("Hello")

    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([inspect]))) as client:
        assert client.post("/chat", json={"message": "Hello"}).json()["status"] == "completed"
        assert client.get("/installations").json() == []


def test_startup_removes_old_automatic_registrations() -> None:
    app = create_app(settings=_settings(), llm=ScriptedLLM([]))
    with TestClient(app) as client:
        for aina_id in ("unibot-code-runner", "unibot.image-recognition"):
            stale = unibot_memory_record()
            stale.manifest.aina.id = aina_id
            _call(client, app.state.repository.register_aina, stale)
        _call(client, ensure_builtin_ainas, app.state.repository)
        assert client.get("/ainas/unibot-code-runner").status_code == 404
        assert client.get("/ainas/unibot.image-recognition").status_code == 404
