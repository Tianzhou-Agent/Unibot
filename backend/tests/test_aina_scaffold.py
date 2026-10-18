"""Creating an AINA (design §12): ``aina init``, developer tokens and ``aina publish``."""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.support.fake_llm import ScriptedLLM
from tests.test_aina_package import _call
from tianzhou_agent_platform.aina.package import cli
from tianzhou_agent_platform.aina.package.mcp_client import McpToolClient
from tianzhou_agent_platform.aina.package.validation import validate_package_files
from tianzhou_agent_platform.aina.project import build_project_archive
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app

AINA_ID = "com.acme.notes"
MCP_URL = "http://127.0.0.1:8770/mcp"


def _init(path: Path, *extra: str) -> int:
    return cli.main(["init", str(path), "--id", AINA_ID, "--name", "Acme Notes", *extra])


def _server(project: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"scaffold_server_{id(project)}", project / "server" / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _folder(path: Path) -> dict[str, bytes]:
    return {file.relative_to(path).as_posix(): file.read_bytes() for file in path.rglob("*") if file.is_file()}


def _settings(**overrides: Any) -> AgentSettings:
    overrides.setdefault("env", "production")
    overrides.setdefault("dev_auth_bypass", False)
    return AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        auth_secret=SecretStr("test-auth-secret-with-enough-entropy"),
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        aina_host_api_base="http://testserver/aina-host/v1",
        **overrides,
    )


# --- aina init ----------------------------------------------------------------------------------------------------


def test_init_creates_a_valid_project_whose_server_works_in_unibot(tmp_path: Path) -> None:
    project = tmp_path / "notes"
    assert _init(project) == 0

    report = validate_package_files(_folder(project / "package"))
    assert report.valid, report.errors
    assert sorted(path.name for path in project.iterdir()) == ["README.md", "package", "server"]
    assert (project / "server" / "requirements.txt").read_text().splitlines() == ["mcp>=2.3,<3", "unibot-aina>=0.1"]

    server = _server(project)
    app = create_app(
        settings=_settings(env="development"),
        llm=ScriptedLLM([]),
        mcp_client=McpToolClient(servers={MCP_URL: server.mcp}),
    )
    with TestClient(app) as client:
        archive = build_project_archive(_folder(project / "package"))
        registered = client.post("/ainas/packages", files={"file": ("notes.aina.zip", archive, "application/zip")})
        client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": ["storage"]})
        added = client.post(
            f"/ainas/{AINA_ID}/app-tools/add_note", json={"arguments": {"text": "Buy milk"}, "confirmed": True}
        )
        listed = client.post(f"/ainas/{AINA_ID}/app-tools/list_notes", json={"arguments": {}})
        canvas = client.get(f"/aina-ui/{AINA_ID}/0.1.0/canvas/index.html")

    assert registered.status_code == 201, registered.text
    tools = {tool["name"]: tool["effect"] for tool in registered.json()["manifest"]["runtime"]["tools"]}
    assert tools == {"list_notes": "read", "add_note": "write", "delete_note": "destructive"}
    assert added.status_code == 200, added.text
    # The note went through the Host API into the AINA's private storage, and back.
    assert [note["text"] for note in listed.json()["structuredContent"]["items"]] == ["Buy milk"]
    assert canvas.status_code == 200


def test_init_refuses_a_non_empty_folder_and_a_bad_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "taken").mkdir()
    (tmp_path / "taken" / "file.txt").write_text("x")

    assert _init(tmp_path / "taken") == 1
    assert "not empty" in capsys.readouterr().err
    assert cli.main(["init", str(tmp_path / "bad"), "--id", "Not An Id", "--name", "Bad"]) == 1
    assert "aina_id" in capsys.readouterr().err
    assert not (tmp_path / "bad").exists()


def test_the_template_ships_the_published_ui_sdk() -> None:
    sdk = Path(__file__).resolve().parents[2] / "spec" / "aina" / "2.0" / "ui"
    template = Path(cli.__file__).with_name("template") / "package"
    for folder in ("canvas", "widgets"):
        for name in ("aina-ui.js", "aina-ui.css"):
            assert (template / folder / name).read_bytes() == (sdk / name).read_bytes()


# --- developer tokens and aina publish ----------------------------------------------------------------------------


@pytest.fixture
def host(tmp_path: Path) -> Any:
    project = tmp_path / "notes"
    _init(project)
    app = create_app(
        settings=_settings(capability_allowed_origins="http://127.0.0.1:8770"),
        llm=ScriptedLLM([]),
        mcp_client=McpToolClient(servers={MCP_URL: _server(project).mcp}),
        enforce_auth=True,
    )
    with TestClient(app) as client:
        response = client.post(
            "/auth/register", json={"name": "Dev", "email": "dev@example.com", "password": "correct-horse-battery"}
        )
        app.state.settings.admin_identities = response.json()["user"]["id"]
        yield client, app, project


def _token(client: TestClient, name: str = "laptop") -> dict[str, Any]:
    created = client.post("/developer-tokens", json={"name": name})
    assert created.status_code == 201, created.text
    return dict(created.json())


def test_developer_tokens_are_shown_once_and_can_be_revoked(host: Any) -> None:
    client, _app, _project = host
    created = _token(client)
    listed = client.get("/developer-tokens").json()
    revoked = client.delete(f"/developer-tokens/{created['record']['id']}")

    assert created["token"].startswith("ubt_") and created["record"]["prefix"] == created["token"][:12]
    assert [item["name"] for item in listed] == ["laptop"]
    assert "token_hash" not in listed[0] and "token" not in listed[0]
    assert revoked.status_code == 204
    assert client.get("/developer-tokens").json() == []


def test_a_developer_token_only_reaches_package_publishing(host: Any) -> None:
    client, app, project = host
    token = _token(client)["token"]
    expired = _token(client, "old")
    record = _call(client, app.state.repository.find_developer_token, _hash(expired["token"]))
    _call(
        client,
        app.state.repository.create_developer_token,
        record.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}),
    )
    client.post("/auth/logout")
    archive = build_project_archive(_folder(project / "package"))
    bearer = {"Authorization": f"Bearer {token}"}

    def publish(headers: dict[str, str]) -> httpx.Response:
        return client.post(
            "/ainas/packages", files={"file": ("n.aina.zip", archive, "application/zip")}, headers=headers
        )

    elsewhere = client.get("/developer-tokens", headers=bearer)
    tokens_cannot_mint_tokens = client.post("/developer-tokens", json={"name": "x"}, headers=bearer)
    stale = publish({"Authorization": f"Bearer {expired['token']}"})
    published = publish(bearer)

    assert elsewhere.status_code == 401 and tokens_cannot_mint_tokens.status_code == 401
    assert stale.status_code == 401
    assert published.status_code == 201, published.text
    listed = _call(client, app.state.repository.find_developer_token, _hash(token))
    assert listed.last_used_at is not None


def test_a_member_token_cannot_publish(host: Any) -> None:
    client, app, project = host
    app.state.settings.admin_identities = ""
    token = _token(client)["token"]
    client.post("/auth/logout")
    archive = build_project_archive(_folder(project / "package"))

    denied = client.post(
        "/ainas/packages",
        files={"file": ("n.aina.zip", archive, "application/zip")},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert denied.status_code == 403


def test_aina_publish_registers_the_package_folder(
    host: Any, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client, _app, project = host
    token = _token(client)["token"]
    client.post("/auth/logout")
    requests: list[str] = []

    def post(url: str, **kwargs: Any) -> httpx.Response:
        requests.append(url)
        kwargs.pop("timeout")
        return client.post(url.removeprefix("http://unibot.test/api"), **kwargs)

    monkeypatch.setattr(cli.httpx, "post", post)
    monkeypatch.setenv("UNIBOT_TOKEN", token)

    assert cli.main(["publish", str(project / "package"), "--host", "http://unibot.test/api/"]) == 0
    assert requests == ["http://unibot.test/api/ainas/packages"]
    assert f"published {AINA_ID} 0.1.0" in capsys.readouterr().out

    monkeypatch.setenv("UNIBOT_TOKEN", "ubt_wrong")
    assert cli.main(["publish", str(project / "package"), "--host", "http://unibot.test/api"]) == 1
    assert "publishing failed (401)" in capsys.readouterr().err


def _hash(token: str) -> str:
    import hashlib

    return hashlib.sha256(token.encode()).hexdigest()
