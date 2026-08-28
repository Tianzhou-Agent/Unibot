from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.sandbox.drivers import LocalProcessSandboxDriver
from tianzhou_agent_platform.sandbox.models import SandboxEnsureRequest
from tianzhou_agent_platform.sandbox.service import SandboxService
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool


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


@pytest.mark.asyncio
async def test_sandbox_service_reuses_driver_file_contract(tmp_path) -> None:
    service = SandboxService(
        InMemoryRepository(),
        LocalProcessSandboxDriver(tmp_path),
        default_image="unibot/sandboxd:test",
    )

    await service.write_file(
        user_id="user-1",
        tenant_id="tenant-1",
        path="managed-ainas/request.json",
        content=b'{"name":"Ada"}',
    )
    content = await service.read_file(
        user_id="user-1",
        tenant_id="tenant-1",
        path="managed-ainas/request.json",
    )
    await service.delete_file(
        user_id="user-1",
        tenant_id="tenant-1",
        path="managed-ainas/request.json",
    )

    assert content == b'{"name":"Ada"}'
    with pytest.raises(PlatformError, match="escapes the workspace"):
        await service.write_file(
            user_id="user-1",
            tenant_id="tenant-1",
            path="../outside",
            content=b"unsafe",
        )


def test_workspace_sandboxes_isolate_files_executions_and_reset(tmp_path: Path) -> None:
    repository = InMemoryRepository()
    driver = LocalProcessSandboxDriver(
        tmp_path / "runtime",
        persistent_workspace_root=tmp_path / "nas" / "workspaces",
    )
    service = SandboxService(repository, driver, default_image="unibot/sandboxd:test")
    app = create_app(
        settings=AgentSettings(sandbox_driver="local", sandbox_workspace_root=tmp_path / "runtime"),
        repository=repository,
        sandbox_service=service,
        llm=ScriptedLLM([]),
    )

    with TestClient(app) as client:
        workspace_a = client.post("/workspaces", json={"name": "Workspace A"}).json()
        workspace_b = client.post("/workspaces", json={"name": "Workspace B"}).json()
        denied = client.post(
            "/sandboxes/ensure",
            json={"workspace_id": workspace_a["id"], "user_id": "other-user"},
        )

        execution_a = client.post(
            "/sandboxes/execute",
            json={
                "workspace_id": workspace_a["id"],
                "language": "python",
                "script": "from pathlib import Path; Path('scope.txt').write_text('A')",
            },
        ).json()
        execution_b = client.post(
            "/sandboxes/execute",
            json={
                "workspace_id": workspace_b["id"],
                "language": "python",
                "script": "from pathlib import Path; Path('scope.txt').write_text('B')",
            },
        ).json()
        standalone_execution = client.post(
            "/sandboxes/execute",
            json={
                "language": "python",
                "script": "from pathlib import Path; Path('scope.txt').write_text('standalone')",
            },
        ).json()

        current_a = client.get(
            "/sandboxes/current",
            params={"workspace_id": workspace_a["id"]},
        ).json()
        current_b = client.get(
            "/sandboxes/current",
            params={"workspace_id": workspace_b["id"]},
        ).json()
        current_standalone = client.get("/sandboxes/current").json()
        history_a = client.get(
            "/sandboxes/executions",
            params={"workspace_id": workspace_a["id"]},
        ).json()
        history_b = client.get(
            "/sandboxes/executions",
            params={"workspace_id": workspace_b["id"]},
        ).json()
        standalone_history = client.get("/sandboxes/executions").json()

        reset_a = client.delete(
            "/sandboxes/current",
            params={"workspace_id": workspace_a["id"]},
        )
        missing_a = client.get(
            "/sandboxes/current",
            params={"workspace_id": workspace_a["id"]},
        )
        remaining_b = client.get(
            "/sandboxes/current",
            params={"workspace_id": workspace_b["id"]},
        )
        remaining_standalone = client.get("/sandboxes/current")
        history_a_after_reset = client.get(
            "/sandboxes/executions",
            params={"workspace_id": workspace_a["id"]},
        ).json()
        history_b_after_reset = client.get(
            "/sandboxes/executions",
            params={"workspace_id": workspace_b["id"]},
        ).json()
        standalone_history_after_reset = client.get("/sandboxes/executions").json()

    files_a = tmp_path / "nas" / "workspaces" / workspace_a["storage_key"] / "files"
    files_b = tmp_path / "nas" / "workspaces" / workspace_b["storage_key"] / "files"
    assert (files_a / "scope.txt").read_text() == "A"
    assert (files_b / "scope.txt").read_text() == "B"
    assert denied.status_code == 403
    assert current_a["workspace"] == str(files_a.resolve())
    assert current_b["workspace"] == str(files_b.resolve())
    assert current_a["workspace_storage_key"] == workspace_a["storage_key"]
    assert current_b["workspace_storage_key"] == workspace_b["storage_key"]
    assert len({current_a["runtime_name"], current_b["runtime_name"], current_standalone["runtime_name"]}) == 3
    assert [item["id"] for item in history_a] == [execution_a["id"]]
    assert [item["id"] for item in history_b] == [execution_b["id"]]
    assert [item["id"] for item in standalone_history] == [standalone_execution["id"]]
    assert all(item["workspace_id"] == workspace_a["id"] for item in history_a)
    assert reset_a.status_code == 204
    assert missing_a.status_code == 404
    assert remaining_b.status_code == 200
    assert remaining_standalone.status_code == 200
    assert history_a_after_reset == []
    assert [item["id"] for item in history_b_after_reset] == [execution_b["id"]]
    assert [item["id"] for item in standalone_history_after_reset] == [standalone_execution["id"]]
    assert (files_a / "scope.txt").read_text() == "A"
    assert (Path(current_standalone["workspace"]) / "scope.txt").read_text() == "standalone"


def test_chat_code_runner_uses_conversation_workspace(tmp_path: Path) -> None:
    repository = InMemoryRepository()
    service = SandboxService(
        repository,
        LocalProcessSandboxDriver(
            tmp_path / "runtime",
            persistent_workspace_root=tmp_path / "nas" / "workspaces",
        ),
        default_image="unibot/sandboxd:test",
    )
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="builtin_sandbox_run_python_",
                arguments=(
                    '{"script":"from pathlib import Path; '
                    "Path('chat.txt').write_text('workspace-chat'); print('done')\"}"
                ),
                call_id="call_run_python",
            ),
            assistant("The workspace code completed."),
        ]
    )
    app = create_app(
        settings=AgentSettings(sandbox_driver="local", sandbox_workspace_root=tmp_path / "runtime"),
        repository=repository,
        sandbox_service=service,
        llm=llm,
    )

    with TestClient(app) as client:
        workspace = client.post("/workspaces", json={"name": "Chat workspace"}).json()
        pending = client.post(
            "/chat",
            json={
                "message": "Run Python and save chat.txt",
                "workspace_id": workspace["id"],
                "capability": "aina:unibot-code-runner",
            },
        )
        confirmed = client.post(
            f"/approvals/{pending.json()['approval']['id']}/confirm",
            json={},
        )
        workspace_history = client.get(
            "/sandboxes/executions",
            params={"workspace_id": workspace["id"]},
        ).json()
        standalone_history = client.get("/sandboxes/executions").json()

    workspace_file = (
        tmp_path
        / "nas"
        / "workspaces"
        / workspace["storage_key"]
        / "files"
        / "chat.txt"
    )
    assert pending.json()["status"] == "approval_required"
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "completed"
    assert len(workspace_history) == 1
    assert workspace_history[0]["workspace_id"] == workspace["id"]
    assert standalone_history == []
    assert workspace_file.read_text() == "workspace-chat"


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
            json={"language": "bash", "script": "printf 'Bash 中文输出'"},
        )
    assert response.status_code == 200
    assert response.json()["stdout"] == "Bash 中文输出"


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
            json={"language": "node", "script": "process.stdout.write('Node.js 中文输出')"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"
    assert response.json()["stdout"] == "Node.js 中文输出"


def test_sandbox_preserves_utf8_file_content_and_output(tmp_path) -> None:
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
                "script": (
                    "from pathlib import Path\n"
                    "content = '这是保存在用户工作区的内容'\n"
                    "Path('中文文件.txt').write_text(content, encoding='utf-8')\n"
                    "print(Path('中文文件.txt').read_text(encoding='utf-8'))"
                ),
            },
        )
    assert response.status_code == 200
    assert response.json()["stdout"].strip() == "这是保存在用户工作区的内容"


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
