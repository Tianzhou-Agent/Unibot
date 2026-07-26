from __future__ import annotations

import asyncio
import shutil

import pytest
from fastapi.testclient import TestClient

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.sandbox.drivers import LocalProcessSandboxDriver
from tianzhou_agent_platform.sandbox.models import SandboxEnsureRequest
from tianzhou_agent_platform.sandbox.service import SandboxService
from tests.support.fake_llm import ScriptedLLM


def test_sandbox_executes_python_and_preserves_workspace(tmp_path) -> None:
    repository = InMemoryRepository()
    app = create_app(
        settings=AgentSettings(
            sandbox_driver="local",
            sandbox_workspace_root=tmp_path,
        ),
        repository=repository,
        llm=ScriptedLLM([]),
    )

    with TestClient(app) as client:
        aina_response = client.get("/ainas/unibot-code-runner")
        assert aina_response.status_code == 200
        assert {item["id"] for item in aina_response.json()["manifest"]["capabilities"]["tools"]} == {
            "sandbox.run_python",
            "sandbox.run_bash",
            "sandbox.run_node",
        }

        ensured = client.post("/sandboxes/ensure", json={})
        assert ensured.status_code == 200
        assert ensured.json()["status"] == "ready"
        assert ensured.json()["driver"] == "local"

        first = client.post(
            "/sandboxes/execute",
            json={
                "language": "python",
                "script": (
                    "from pathlib import Path\n"
                    "Path('answer.txt').write_text('42', encoding='utf-8')\n"
                    "Path('.python-packages').mkdir(exist_ok=True)\n"
                    "Path('.python-packages/workspace_dep.py').write_text(\"VALUE = 'installed'\", encoding='utf-8')\n"
                    "print('created')"
                ),
            },
        )
        assert first.status_code == 200
        assert first.json()["status"] == "succeeded"
        assert first.json()["stdout"].strip() == "created"

        second = client.post(
            "/sandboxes/execute",
            json={
                "language": "python",
                "script": (
                    "from pathlib import Path; import workspace_dep; "
                    "print(f\"{Path('answer.txt').read_text(encoding='utf-8')}:{workspace_dep.VALUE}\")"
                ),
            },
        )
        assert second.status_code == 200
        assert second.json()["stdout"].strip() == "42:installed"

        history = client.get("/sandboxes/executions")
        assert history.status_code == 200
        assert [item["id"] for item in history.json()] == [
            second.json()["id"],
            first.json()["id"],
        ]

        stopped = client.post("/sandboxes/stop", json={})
        assert stopped.status_code == 200
        assert stopped.json()["status"] == "stopped"

        reset = client.delete("/sandboxes/current")
        assert reset.status_code == 204
        assert client.get("/sandboxes/current").status_code == 404
        assert client.get("/sandboxes/executions").json() == []


def test_sandbox_rejects_workspace_escape(tmp_path) -> None:
    app = create_app(
        settings=AgentSettings(sandbox_driver="local", sandbox_workspace_root=tmp_path),
        repository=InMemoryRepository(),
        llm=ScriptedLLM([]),
    )
    with TestClient(app) as client:
        response = client.post(
            "/sandboxes/execute",
            json={
                "language": "python",
                "script": "print('unsafe')",
                "working_directory": "../outside",
            },
        )
    assert response.status_code == 422


def test_sandbox_rejects_user_supplied_runtime_image(tmp_path) -> None:
    app = create_app(
        settings=AgentSettings(sandbox_driver="local", sandbox_workspace_root=tmp_path),
        repository=InMemoryRepository(),
        llm=ScriptedLLM([]),
    )
    with TestClient(app) as client:
        response = client.post(
            "/sandboxes/ensure",
            json={"image": "attacker.example/untrusted:latest"},
        )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_concurrent_sandbox_ensure_reuses_one_actor_record(tmp_path) -> None:
    repository = InMemoryRepository()
    service = SandboxService(
        repository,
        LocalProcessSandboxDriver(tmp_path),
        default_image="unibot/sandboxd:test",
    )

    records = await asyncio.gather(
        *[service.ensure(SandboxEnsureRequest(user_id="same-user")) for _ in range(5)]
    )

    assert len({record.id for record in records}) == 1


def test_sandbox_executes_bash_when_available(tmp_path) -> None:
    if shutil.which("bash") is None:
        return
    app = create_app(
        settings=AgentSettings(sandbox_driver="local", sandbox_workspace_root=tmp_path),
        repository=InMemoryRepository(),
        llm=ScriptedLLM([]),
    )
    with TestClient(app) as client:
        response = client.post(
            "/sandboxes/execute",
            json={"language": "bash", "script": "printf 'bash-ready'"},
        )
    assert response.status_code == 200
    assert response.json()["stdout"] == "bash-ready"


def test_sandbox_executes_node_when_available(tmp_path) -> None:
    if shutil.which("node") is None:
        return
    app = create_app(
        settings=AgentSettings(sandbox_driver="local", sandbox_workspace_root=tmp_path),
        repository=InMemoryRepository(),
        llm=ScriptedLLM([]),
    )
    with TestClient(app) as client:
        response = client.post(
            "/sandboxes/execute",
            json={"language": "node", "script": "process.stdout.write('node-ready')"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["stdout"] == "node-ready"


def test_sandbox_bounds_captured_output(tmp_path) -> None:
    app = create_app(
        settings=AgentSettings(
            sandbox_driver="local",
            sandbox_workspace_root=tmp_path,
            sandbox_output_limit_bytes=1_024,
        ),
        repository=InMemoryRepository(),
        llm=ScriptedLLM([]),
    )
    with TestClient(app) as client:
        response = client.post(
            "/sandboxes/execute",
            json={"language": "python", "script": "print('x' * 4096, end='')"},
        )
    assert response.status_code == 200
    assert response.json()["truncated"] is True
    assert len(response.json()["stdout"].encode()) == 1_024
