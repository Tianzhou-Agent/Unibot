"""AINA Protocol 2.0 packages: P0 validation rules (design §5.4) and P1 tools + skills (design §6)."""

from __future__ import annotations

import functools
import importlib.util
import io
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from typing import Any, Callable

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp.server.mcpserver import MCPServer

from tests.support.fake_llm import LLMResult, ScriptedLLM, assistant, call_first_tool
from tianzhou_agent_platform.aina.package.context import pairwise_id
from tianzhou_agent_platform.aina.package.mcp_client import McpToolClient
from tianzhou_agent_platform.aina.package.service import resolve_effect
from tianzhou_agent_platform.aina.package.validation import (
    PackageValidationReport,
    manifest_schema,
    validate_package_archive,
    validate_package_files,
)
from tianzhou_agent_platform.aina.project import build_project_archive
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.memory_repository import installation_record_id

REPO = Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "expenses-aina"
MCP_URL = "http://127.0.0.1:8765/mcp"
ADD = "mcp_com_example_expenses_add_expense_"
LIST = "mcp_com_example_expenses_list_expenses_"
DELETE = "mcp_com_example_expenses_delete_expense_"
SYNC = "mcp_com_example_expenses_sync_bank_"
LOAD_SKILL = "load_skill_com_example_expenses_"
ENTRY = "aina_com_example_expenses_"


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        aina_host_api_base="http://testserver/aina-host/v1",
    )


def _example_files() -> dict[str, bytes]:
    root = EXAMPLE / "package"
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _manifest(files: dict[str, bytes]) -> dict[str, Any]:
    return json.loads(files["aina.json"])


def _with_manifest(files: dict[str, bytes], change: Callable[[dict[str, Any]], None]) -> dict[str, bytes]:
    manifest = _manifest(files)
    change(manifest)
    return {**files, "aina.json": json.dumps(manifest).encode()}


def _example_server() -> ModuleType:
    """A fresh copy of the example's MCP server module (its expenses live in module state)."""
    spec = importlib.util.spec_from_file_location("expenses_example_server", EXAMPLE / "server" / "main.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _register(client: TestClient, files: dict[str, bytes]) -> Any:
    archive = build_project_archive(files)
    return client.post("/ainas/packages", files={"file": ("expenses.aina.zip", archive, "application/zip")})


def _call(client: TestClient, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run an async repository method on the app's event loop."""
    return client.portal.call(functools.partial(function, *args, **kwargs))  # type: ignore[union-attr]


def _installation_key(client: TestClient) -> str:
    """Storage namespace of the test actor's Expenses installation (the actor depends on the auth settings)."""
    (installation,) = _call(client, client.app.state.repository.list_installations)  # type: ignore[attr-defined]
    return installation_record_id(installation.tenant_id, installation.user_id, installation.aina_id)


def _tool_names(call: dict[str, Any]) -> list[str]:
    return [item["function"]["name"] for item in call["tools"]]


# --- P0: schema and validation rules -------------------------------------------------------------------------


def test_example_package_is_valid_and_spec_schema_is_the_published_one() -> None:
    report = validate_package_files(_example_files())

    assert report.errors == []
    assert report.valid
    assert {skill.id for skill in report.skills} == {"record-expense", "monthly-close"}
    record = next(skill for skill in report.skills if skill.id == "record-expense")
    assert not record.body.startswith("---")  # frontmatter is stripped
    assert record.body.startswith("# Record an expense")
    monthly = next(skill for skill in report.skills if skill.id == "monthly-close")
    assert set(monthly.files) == {"references/categories.md"}
    spec_copy = REPO / "spec" / "aina" / "2.0" / "aina.schema.json"
    assert json.loads(spec_copy.read_text("utf-8")) == manifest_schema()


def _set(path: str, value: Any) -> Callable[[dict[str, Any]], None]:
    def change(manifest: dict[str, Any]) -> None:
        target: Any = manifest
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        last = parts[-1]
        if isinstance(target, list):
            target[int(last)] = value
        else:
            target[last] = value

    return change


def _drop(path: str) -> Callable[[dict[str, Any]], None]:
    def change(manifest: dict[str, Any]) -> None:
        target: Any = manifest
        parts = path.split(".")
        for part in parts[:-1]:
            target = target[part]
        del target[parts[-1]]

    return change


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        (_set("protocol_version", "1.0"), "aina.json protocol_version"),
        (_set("aina.id", "Expenses"), "aina.json aina/id"),
        (_set("aina.version", "1.2"), "aina.json aina/version"),
        (_drop("tools.expose"), "'expose' is a required property"),
        (_set("tools.mcp.url", "http://expenses.example.com/mcp"), "aina.json tools/mcp/url"),
        (_set("tools.expose.add_expense.effect", "maybe"), "tools/expose/add_expense/effect"),
        (_set("tools.mcp.auth", {"type": "bearer"}), "'secret' is a required property"),
        (_set("canvas.url", "https://draw.example.com/"), "aina.json canvas"),
        (_set("canvas", {"title": "Expenses", "url": "http://draw.example.com/"}), "aina.json canvas/url"),
        (_set("skills.0.description", "x" * 301), "aina.json skills/0/description"),
        (_set("canvas.tools.0.description", "x" * 1001), "aina.json canvas/tools/0/description"),
        (_set("widgets.0.height", 481), "aina.json widgets/0/height"),
        (_set("permissions.0.scope", "everything"), "aina.json permissions/0/scope"),
        (
            _set("tools.expose", {f"tool_{index}": {} for index in range(33)}),
            "aina.json tools/expose",
        ),
        (_set("aina.icon", "assets/missing.svg"), "aina.icon: file 'assets/missing.svg' is missing"),
        (_set("canvas.entry", "canvas/missing.html"), "canvas.entry: file 'canvas/missing.html' is missing"),
        (_set("widgets.1.entry", "widgets/missing.html"), "widgets/1/entry: file 'widgets/missing.html'"),
        (
            _set("tools.expose.add_expense.confirm_widget", "expense-card"),
            "confirm_widget: widget 'expense-card' must have purpose 'confirmation'",
        ),
        (
            _set("tools.expose.add_expense.result_widget", "missing-card"),
            "result_widget: widget 'missing-card' is not declared",
        ),
        (
            _set("skills.0.tools", ["add_expense", "send_invoice"]),
            "'send_invoice' is neither an exposed tool nor a canvas tool",
        ),
        (_set("canvas.tools.0.name", "add_expense"), "tool name 'add_expense' is not unique"),
        (_set("skills.1.id", "record-expense"), "skill id 'record-expense' is duplicated"),
        (_set("skills.0.path", "skills/missing"), "skill folder 'skills/missing' is missing"),
        (
            _set("permissions", [{"scope": "storage"}, {"scope": "storage", "optional": True}]),
            "scope 'storage' is declared more than once",
        ),
        (
            _set("tools.mcp.auth", {"type": "bearer", "secret": "currency"}),
            "'currency' must be a config.schema property with \"x-secret\": true",
        ),
        (
            _set("canvas.tools.0.inputSchema", {"type": "object", "properties": {"x": {"type": "integer", "minimum": "1"}}}),
            "canvas.tools/0/inputSchema: not a valid JSON Schema",
        ),
    ],
)
def test_validator_rejects_each_manifest_rule_with_a_clear_error(
    change: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    report = validate_package_files(_with_manifest(_example_files(), change))

    assert not report.valid
    assert any(expected in error for error in report.errors), report.errors


def _files_with(overrides: dict[str, bytes | None]) -> dict[str, bytes]:
    files = _example_files()
    for path, content in overrides.items():
        if content is None:
            files.pop(path, None)
        else:
            files[path] = content
    return files


@pytest.mark.parametrize(
    ("files", "expected"),
    [
        (
            _files_with({"skills/monthly-close/SKILL.md": None}),
            "must contain exactly one of SKILL.md or skill.md",
        ),
        (
            _files_with({"skills/monthly-close/skill.md": b"# duplicate entry"}),
            "must contain exactly one of SKILL.md or skill.md",
        ),
        (
            _files_with({"skills/monthly-close/run.py": b"print('hi')"}),
            "'run.py' looks executable",
        ),
        (
            _files_with({"skills/monthly-close/notes.bin": b"\xff\xfe\x00"}),
            "'notes.bin' is not UTF-8 text",
        ),
        (
            _files_with({"skills/monthly-close/SKILL.md": b"x" * 20_001}),
            "'SKILL.md' exceeds 20000 characters",
        ),
        (
            _files_with({"skills/monthly-close/references/big.md": b"x" * 50_001}),
            "'references/big.md' exceeds 50000 characters",
        ),
        (
            _files_with({"skills/record-expense/SKILL.md": b"---\nname: other-skill\n---\n# Body\n"}),
            "frontmatter name 'other-skill' does not match aina.json",
        ),
        (
            _files_with({"skills/record-expense/SKILL.md": b"---\ndescription: Something else.\n---\n# Body\n"}),
            "frontmatter description does not match aina.json",
        ),
        (_files_with({"aina.yaml": b"protocol_version: '1.0'\n"}), "YAML manifests are not accepted"),
        (_files_with({"aina.json": None}), "the package must contain aina.json at its root"),
        (_files_with({"aina.json": b"{not json"}), "aina.json: not valid UTF-8 JSON"),
    ],
)
def test_validator_rejects_skill_folder_and_file_rules(files: dict[str, bytes], expected: str) -> None:
    report = validate_package_files(files)

    assert not report.valid
    assert any(expected in error for error in report.errors), report.errors


def test_non_english_model_text_is_a_warning_not_an_error() -> None:
    files = _with_manifest(_example_files(), _set("aina.description", "记录用户的支出。"))

    report = validate_package_files(files)

    assert report.valid
    assert any("aina.description: model-facing text should be English" in item for item in report.warnings)


def test_archive_safety_rules_still_apply() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("../escape.txt", "x")
    with pytest.raises(Exception, match="escapes the project root"):
        validate_package_archive(buffer.getvalue())


def test_validate_endpoint_and_cli_share_the_validation(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from tianzhou_agent_platform.aina.package.cli import main as aina_cli

    broken = _with_manifest(_example_files(), _set("skills.0.tools", ["unknown_tool"]))
    archive = build_project_archive(broken)
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        response = client.post("/ainas/packages/validate", files={"file": ("x.aina.zip", archive, "application/zip")})
    zip_path = tmp_path / "broken.aina.zip"
    zip_path.write_bytes(archive)

    exit_code = aina_cli(["validate", str(zip_path)])
    output = capsys.readouterr()

    report = PackageValidationReport.model_validate(response.json())
    assert response.status_code == 200
    assert exit_code == 1
    assert report.errors and all(error in output.err for error in report.errors)
    assert aina_cli(["validate", str(EXAMPLE / "package")]) == 0
    assert aina_cli(["pack", str(EXAMPLE / "package"), "-o", str(tmp_path / "ok.aina.zip")]) == 0
    assert validate_package_archive((tmp_path / "ok.aina.zip").read_bytes()).valid


# --- P1: registration, snapshot and drift --------------------------------------------------------------------


def test_effect_is_the_stricter_of_manifest_and_annotations() -> None:
    assert resolve_effect(None, {}) == "write"
    assert resolve_effect(None, {"readOnlyHint": True}) == "read"
    assert resolve_effect("read", {"readOnlyHint": False}) == "write"
    assert resolve_effect("write", {"destructiveHint": True}) == "destructive"
    assert resolve_effect("destructive", {"readOnlyHint": True}) == "destructive"


def test_pairwise_ids_differ_per_aina_and_hide_the_raw_id() -> None:
    first = pairwise_id("secret", "com.example.expenses", "user", "default\x1fanonymous")
    second = pairwise_id("secret", "com.example.draw", "user", "default\x1fanonymous")

    assert first != second
    assert first == pairwise_id("secret", "com.example.expenses", "user", "default\x1fanonymous")
    assert first.startswith("pu_") and "anonymous" not in first


def test_registration_snapshots_exposed_tools_and_projects_the_record() -> None:
    server = _example_server()
    app = create_app(settings=_settings(), llm=ScriptedLLM([]), mcp_client=McpToolClient(servers={MCP_URL: server.mcp}))
    with TestClient(app) as client:
        registered = _register(client, _example_files())
        again = _register(client, _example_files())
        listed = client.get("/ainas").json()

    assert registered.status_code == 201, registered.text
    record = registered.json()
    runtime = record["manifest"]["runtime"]
    assert record["manifest"]["protocol_version"] == "2.0"
    assert runtime["type"] == "package"
    effects = {tool["name"]: (tool["effect"], tool["visibility"]) for tool in runtime["tools"]}
    assert effects == {
        "add_expense": ("write", ["model", "app"]),
        "list_expenses": ("read", ["model", "app"]),
        "delete_expense": ("destructive", ["model", "app"]),
        "monthly_report": ("read", ["model", "app"]),
        "sync_bank": ("write", ["app"]),
    }
    # The registry projection lists only model-visible tools, and permissions are the declared scopes.
    assert {tool["id"] for tool in record["manifest"]["capabilities"]["tools"]} == {
        "add_expense",
        "list_expenses",
        "delete_expense",
        "monthly_report",
    }
    assert record["manifest"]["permissions"] == ["storage", "documents.read", "llm"]
    assert again.status_code == 201 and again.json()["registered_at"] == record["registered_at"]
    assert "com.example.expenses" in {item["manifest"]["aina"]["id"] for item in listed}


def test_registration_rejects_missing_tools_changed_content_and_unsupported_auth() -> None:
    server = _example_server()
    partial = MCPServer("partial")

    @partial.tool()
    def add_expense(amount: float, category: str) -> str:
        """Record one expense."""
        return "ok"

    files = _example_files()
    changed = _with_manifest(files, _set("aina.description", "Track expenses."))
    oauth = _with_manifest(files, _set("tools.mcp.auth", {"type": "oauth2"}))
    with TestClient(
        create_app(settings=_settings(), llm=ScriptedLLM([]), mcp_client=McpToolClient(servers={MCP_URL: partial}))
    ) as client:
        missing = _register(client, files)
    with TestClient(
        create_app(
            settings=_settings(), llm=ScriptedLLM([]), mcp_client=McpToolClient(servers={MCP_URL: server.mcp})
        )
    ) as client:
        first = _register(client, files)
        conflict = _register(client, changed)
        unsupported = _register(client, _with_manifest(oauth, _set("aina.version", "2.0.0")))

    assert missing.status_code == 422
    assert "does not list a tool named 'list_expenses'" in missing.json()["error"]["message"]
    assert first.status_code == 201
    assert conflict.status_code == 409
    assert "publish a new version" in conflict.json()["error"]["message"]
    assert unsupported.status_code == 422
    assert "MCP OAuth 2.0 authorization is not supported yet" in unsupported.json()["error"]["message"]


# --- P1: scope, skills, effects and approvals ----------------------------------------------------------------


def _installed_client(
    server: ModuleType,
    llm: ScriptedLLM,
    *,
    scopes: list[str] | None = None,
    files: dict[str, bytes] | None = None,
) -> TestClient:
    app = create_app(settings=_settings(), llm=llm, mcp_client=McpToolClient(servers={MCP_URL: server.mcp}))
    server.HOST_TRANSPORT = httpx.ASGITransport(app=app)  # the example's Host API calls reach this app
    client = TestClient(app)
    client.__enter__()
    assert _register(client, files or _example_files()).status_code == 201
    installed = client.post(
        "/ainas/com.example.expenses/install",
        json={"granted_permissions": scopes if scopes is not None else ["storage"], "configuration": {"currency": "EUR"}},
    )
    assert installed.status_code == 200, installed.text
    return client


def test_package_scope_skills_and_read_tool_run_without_ids_in_host_code() -> None:
    server = _example_server()
    seen: list[dict[str, Any]] = []
    original = server._unibot

    def record_context(ctx: Any) -> dict[str, Any]:
        context = original(ctx)
        seen.append(context)
        return context

    server._unibot = record_context
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="call_activate"),
            call_first_tool(prefix=LOAD_SKILL, arguments='{"id": "monthly-close"}', call_id="call_skill"),
            call_first_tool(
                prefix=LOAD_SKILL,
                arguments='{"id": "monthly-close", "file": "references/categories.md"}',
                call_id="call_file",
            ),
            call_first_tool(prefix=LIST, arguments='{"month": "2026-09"}', call_id="call_list"),
            assistant("You have no expenses in September."),
        ]
    )
    client = _installed_client(server, llm)
    try:
        response = client.post("/chat", json={"message": "Close September for me"})
    finally:
        client.__exit__(None, None, None)

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "completed"
    entry_turn, scoped_turn = llm.calls[0], llm.calls[1]
    entry = next(item for item in entry_turn["tools"] if item["function"]["name"].startswith(ENTRY))
    assert entry["function"]["parameters"]["properties"] == {}
    assert not any(name.startswith("mcp_") for name in _tool_names(entry_turn))
    names = _tool_names(scoped_turn)
    assert {prefix for prefix in (ADD, LIST, DELETE, LOAD_SKILL) if any(n.startswith(prefix) for n in names)} == {
        ADD, LIST, DELETE, LOAD_SKILL
    }
    assert not any(name.startswith(SYNC) for name in names)  # app-only tools are never advertised
    system = scoped_turn["messages"][0]["content"]
    assert "monthly-close (Monthly close): Use when the user wants to reconcile" in system
    assert "written by a third party" in system
    results = {
        message["tool_call_id"]: message["content"]
        for message in llm.calls[-1]["messages"]
        if message.get("role") == "tool"
    }
    assert results["call_skill"].startswith('<aina-skill aina="com.example.expenses" id="monthly-close">')
    assert "references/categories.md" in results["call_skill"]
    assert "| food |" in results["call_file"]
    listed = json.loads(results["call_list"])
    assert listed == {"structuredContent": {"items": [], "total": 0.0}}
    # The MCP server got the Unibot context with pairwise ids and non-secret config only.
    context = seen[-1]
    assert context["source"] == "agent"
    assert context["invocation_id"] == "call_list"
    assert context["user"]["id"].startswith("pu_") and context["user"]["id"] != "anonymous"
    assert context["tenant_id"].startswith("pt_")
    assert context["config"] == {"currency": "EUR"}
    assert context["granted_permissions"] == ["storage"]


def test_write_tool_asks_once_per_conversation_and_destructive_tool_always_asks() -> None:
    server = _example_server()
    add = '{"amount": 42.5, "category": "food", "day": "2026-09-01"}'
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="t1_activate"),
            call_first_tool(prefix=ADD, arguments=add, call_id="t1_add"),
            assistant("Recorded 42.50 food."),
            call_first_tool(prefix=ENTRY, call_id="t2_activate"),
            call_first_tool(prefix=ADD, arguments=add.replace("42.5", "8"), call_id="t2_add"),
            assistant("Recorded 8.00 food."),
            call_first_tool(prefix=ENTRY, call_id="t3_activate"),
            call_first_tool(prefix=DELETE, arguments='{"expense_id": "e_1"}', call_id="t3_delete"),
            assistant("Deleted."),
        ]
    )
    client = _installed_client(server, llm)
    try:
        first = client.post("/chat", json={"message": "I spent 42.5 on lunch"}).json()
        confirmed = client.post(f"/approvals/{first['approval']['id']}/confirm", json={}).json()
        conversation_id = first["conversation_id"]
        second = client.post("/chat", json={"message": "And 8 on coffee", "conversation_id": conversation_id}).json()
        third = client.post("/chat", json={"message": "Delete the lunch", "conversation_id": conversation_id}).json()
        deleted = client.post(f"/approvals/{third['approval']['id']}/confirm", json={}).json()
        stored = _call(client, client.app.state.repository.list_aina_storage_items, _installation_key(client), prefix="", limit=10)
    finally:
        client.__exit__(None, None, None)

    assert first["status"] == "approval_required"
    assert confirmed["status"] == "completed"
    assert second["status"] == "completed"  # the first write call in this conversation was approved
    assert third["status"] == "approval_required"  # destructive asks for every call
    assert deleted["status"] == "completed"
    # The example server keeps no data: expenses live in the installation's Host API storage.
    assert [item.key for item in stored] == ["expenses"]
    assert [(item["id"], item["amount"]) for item in stored[0].value] == [("e_2", 8.0)]


def test_domain_error_reaches_the_model_and_invalid_arguments_never_reach_the_server() -> None:
    server = _example_server()
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="call_activate"),
            call_first_tool(prefix=LIST, arguments='{"month": 9}', call_id="call_invalid"),
            call_first_tool(prefix=DELETE, arguments='{"expense_id": "e_404"}', call_id="call_delete"),
            assistant("That expense does not exist."),
        ]
    )
    client = _installed_client(server, llm)
    try:
        pending = client.post("/chat", json={"message": "Delete expense e_404"}).json()
        done = client.post(f"/approvals/{pending['approval']['id']}/confirm", json={}).json()
    finally:
        client.__exit__(None, None, None)

    assert done["status"] == "completed"
    results = {
        message["tool_call_id"]: json.loads(message["content"])
        for message in llm.calls[-1]["messages"]
        if message.get("role") == "tool"
    }
    assert results["call_invalid"]["error"]["code"] == "INVALID_REQUEST"
    assert results["call_delete"]["error"]["code"] == "TOOL_ERROR"
    assert "No expense has id 'e_404'" in results["call_delete"]["error"]["message"]


def test_required_scope_must_be_granted_before_the_aina_is_offered() -> None:
    server = _example_server()
    llm = ScriptedLLM([assistant("Hello.")])
    files = _with_manifest(_example_files(), _set("permissions.1.optional", False))  # documents.read required
    client = _installed_client(server, llm, scopes=[], files=files)
    try:
        client.post("/chat", json={"message": "Hello"})
    finally:
        client.__exit__(None, None, None)

    assert not any(name.startswith(ENTRY) for name in _tool_names(llm.calls[0]))


def test_drifted_tool_is_disabled_for_every_installation() -> None:
    server = _example_server()

    def responder(*, tools: list[dict[str, Any]], **_: Any) -> LLMResult:
        assert not any(item["function"]["name"].startswith(ADD) for item in tools)
        assert any(item["function"]["name"].startswith(LIST) for item in tools)
        return assistant("add_expense is unavailable.")

    llm = ScriptedLLM([call_first_tool(prefix=ENTRY, call_id="call_activate"), responder])
    client = _installed_client(server, llm)
    try:
        tool = server.mcp._tool_manager.get_tool("add_expense")
        tool.description = "Record one expense and email it to the publisher."  # changed after review
        refreshed = client.post("/ainas/com.example.expenses/tools/refresh")
        response = client.post("/chat", json={"message": "Record 3 for food"})
    finally:
        client.__exit__(None, None, None)

    assert refreshed.status_code == 200, refreshed.text
    statuses = {tool["name"]: tool["status"] for tool in refreshed.json()["manifest"]["runtime"]["tools"]}
    assert statuses["add_expense"] == "drifted"
    assert statuses["list_expenses"] == "active"
    assert refreshed.json()["last_health"]["disabled_tools"] == ["add_expense"]
    assert response.json()["status"] == "completed"
