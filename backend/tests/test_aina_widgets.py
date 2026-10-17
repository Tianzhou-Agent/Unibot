"""AINA Protocol 2.0 phase P3b: confirmation widgets in approvals and result widgets in the conversation (design §9.2)."""

from __future__ import annotations

import json
from typing import Any

import httpx
from fastapi.testclient import TestClient

from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool
from tests.test_aina_host_api import AINA_ID, RecordingMcp, _call
from tests.test_aina_host_tasks import REPORT, _finish_jobs
from tests.test_aina_package import (
    ADD,
    DELETE,
    ENTRY,
    MCP_URL,
    _example_files,
    _example_server,
    _manifest,
    _register,
    _set,
    _settings,
    _with_manifest,
)
from tianzhou_agent_platform.aina.package.manifest import AinaPackageManifest
from tianzhou_agent_platform.aina.package.widgets import result_widget_id
from tianzhou_agent_platform.main import create_app

ADD_ARGS = {"amount": 42.5, "category": "food", "day": "2026-10-01"}


def _client(llm: ScriptedLLM, files: dict[str, bytes] | None = None) -> tuple[TestClient, Any, RecordingMcp]:
    server = _example_server()
    mcp = RecordingMcp({MCP_URL: server.mcp})
    app = create_app(settings=_settings(), llm=llm, mcp_client=mcp)
    server.HOST_TRANSPORT = httpx.ASGITransport(app=app)
    client = TestClient(app)
    client.__enter__()
    assert _register(client, files or _example_files()).status_code == 201
    assert client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []}).status_code == 200
    return client, server, mcp


def _add_turn() -> ScriptedLLM:
    return ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="a"),
            call_first_tool(prefix=ADD, arguments=json.dumps(ADD_ARGS), call_id="add"),
            assistant("Recorded."),
        ]
    )


def _stored(client: TestClient) -> list[dict[str, Any]]:
    items = _call(client, client.app.state.repository.list_aina_storage_items, _key(client), prefix="", limit=5)  # type: ignore[attr-defined]
    return list(items[0].value) if items else []


def _key(client: TestClient) -> str:
    from tianzhou_agent_platform.store.memory_repository import installation_record_id

    (installation,) = _call(client, client.app.state.repository.list_installations)  # type: ignore[attr-defined]
    return installation_record_id(installation.tenant_id, installation.user_id, installation.aina_id)


# --- Confirmation widgets --------------------------------------------------------------------------------------------


def test_approval_carries_the_confirmation_widget_and_runs_the_edited_arguments() -> None:
    llm = _add_turn()
    client, _server, mcp = _client(llm)
    try:
        pending = client.post("/chat", json={"message": "I spent 42.5 on lunch"}).json()
        listed = client.get("/approvals", params={"conversation_id": pending["conversation_id"]}).json()
        edited = {**ADD_ARGS, "amount": 40.0, "category": "entertainment"}
        confirmed = client.post(
            f"/approvals/{pending['approval']['id']}/confirm", json={"edited_arguments": {"add": edited}}
        ).json()
        stored = _stored(client)
        trace = client.get(f"/traces/{pending['trace_id']}").json()
    finally:
        client.__exit__(None, None, None)

    (widget,) = pending["approval"]["widgets"]
    assert widget["widget_id"] == "confirm-expense" and widget["purpose"] == "confirmation"
    assert widget["call_id"] == "add" and widget["arguments"] == ADD_ARGS
    assert widget["entry"] == "widgets/confirm-expense.html" and widget["height"] == 160
    assert listed[0]["widgets"] == pending["approval"]["widgets"]  # persisted with the approval
    assert confirmed["status"] == "completed"
    # The edited call ran, and the model is told what actually ran.
    assert [(item["amount"], item["category"]) for item in stored] == [(40.0, "entertainment")]
    assert mcp.metas[-1]["unibot/context"]["source"] == "agent"
    tool_result = next(m["content"] for m in llm.calls[-1]["messages"] if m.get("tool_call_id") == "add")
    assert "40.0" in tool_result and "entertainment" in tool_result
    event = next(item for item in trace["events"] if item["kind"] == "approval.confirmed")
    assert event["details"]["edited_calls"] == ["add"]


def test_invalid_or_unwidgeted_edits_are_refused_and_the_approval_stays_pending() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="a"),
            call_first_tool(prefix=ADD, arguments=json.dumps(ADD_ARGS), call_id="add"),
            assistant("Recorded."),
        ]
    )
    client, _server, _mcp = _client(llm)
    try:
        pending = client.post("/chat", json={"message": "I spent 42.5 on lunch"}).json()
        url = f"/approvals/{pending['approval']['id']}/confirm"
        invalid = client.post(url, json={"edited_arguments": {"add": {**ADD_ARGS, "amount": "lots"}}})
        unknown_field = client.post(url, json={"edited_arguments": {"add": {**ADD_ARGS, "category": None}}})
        other_call = client.post(url, json={"edited_arguments": {"not-a-call": ADD_ARGS}})
        still_pending = client.get("/approvals", params={"conversation_id": pending["conversation_id"]}).json()
        confirmed = client.post(url, json={}).json()
        stored = _stored(client)
    finally:
        client.__exit__(None, None, None)

    for response in (invalid, unknown_field, other_call):
        assert response.status_code in {400, 422}, response.text
    assert [item["status"] for item in still_pending] == ["pending"]
    assert confirmed["status"] == "completed"
    assert [item["amount"] for item in stored] == [42.5]  # the original arguments ran


def test_calls_without_a_confirmation_widget_cannot_be_edited() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="a"),
            call_first_tool(prefix=DELETE, arguments='{"expense_id": "e_1"}', call_id="del"),
            assistant("Deleted."),
        ]
    )
    client, _server, _mcp = _client(llm)
    try:
        pending = client.post("/chat", json={"message": "Delete e_1"}).json()
        edit = client.post(
            f"/approvals/{pending['approval']['id']}/confirm", json={"edited_arguments": {"del": {"expense_id": "e_2"}}}
        )
    finally:
        client.__exit__(None, None, None)

    assert pending["approval"].get("widgets", []) == []
    assert edit.status_code == 422


# --- Result widgets --------------------------------------------------------------------------------------------------


def test_result_widget_is_kept_with_the_answer_and_survives_a_reload() -> None:
    llm = _add_turn()
    client, _server, _mcp = _client(llm)
    try:
        pending = client.post("/chat", json={"message": "I spent 42.5 on lunch"}).json()
        confirmed = client.post(f"/approvals/{pending['approval']['id']}/confirm", json={}).json()
        conversation = client.get(f"/conversations/{pending['conversation_id']}").json()
    finally:
        client.__exit__(None, None, None)

    (widget,) = [item for item in confirmed["widgets"] if item["kind"] == "aina_widget"]
    data = widget["aina_widget"]
    assert (data["widget_id"], data["purpose"], data["tool_name"], data["call_id"]) == (
        "expense-card",
        "result",
        "add_expense",
        "add",
    )
    assert data["structured_content"]["amount"] == 42.5 and data["arguments"] == ADD_ARGS
    answer = conversation["messages"][-1]
    assert answer["content"] == "Recorded."
    assert [item["aina_widget"]["widget_id"] for item in answer["widgets"]] == ["expense-card"]


def test_a_result_can_choose_another_declared_widget_or_none() -> None:
    raw = _manifest(_example_files())
    raw["widgets"].append({"id": "summary", "purpose": "display", "entry": "widgets/expense-card.html"})
    manifest = AinaPackageManifest.model_validate(raw)

    assert result_widget_id(manifest, "add_expense", {}) == "expense-card"
    assert result_widget_id(manifest, "add_expense", {"unibot/widget": None}) is None
    assert result_widget_id(manifest, "list_expenses", {"unibot/widget": "summary"}) == "summary"
    assert result_widget_id(manifest, "add_expense", {"unibot/widget": "made-up"}) is None
    assert result_widget_id(manifest, "add_expense", {"unibot/widget": "confirm-expense"}) is None


def test_a_finished_task_is_posted_with_its_result_widget() -> None:
    files = _with_manifest(_example_files(), _set("tools.expose.monthly_report.result_widget", "expense-card"))
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="a"),
            call_first_tool(prefix=REPORT, call_id="r", arguments='{"month": "2026-09"}'),
        ]
    )
    client, server, _mcp = _client(llm, files)
    try:
        conversation_id = client.post("/chat", json={"message": "Report September"}).json()["conversation_id"]
        _call(client, _finish_jobs, server)
        client.get(f"/conversations/{conversation_id}/aina-tasks")
        posted = client.get(f"/conversations/{conversation_id}").json()["messages"][-1]
    finally:
        client.__exit__(None, None, None)

    (widget,) = posted["widgets"]
    assert widget["kind"] == "aina_widget"
    assert widget["aina_widget"]["tool_name"] == "monthly_report"
    assert widget["aina_widget"]["structured_content"]["month"] == "2026-09"
