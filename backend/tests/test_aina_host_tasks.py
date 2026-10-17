"""AINA Protocol 2.0 long-running tools: background tasks, Host API task callbacks and token refresh (design §7.3, §8)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool
from tests.support.sqlite_store import sqlite_session_factory
from tests.test_aina_host_api import AINA_ID, RecordingMcp, _bearer, _call, _installation, _token
from tests.test_aina_package import (
    ENTRY,
    MCP_URL,
    _example_files,
    _example_server,
    _installation_key,
    _register,
    _settings,
)
from tianzhou_agent_platform.aina.package.storage import storage_item
from tianzhou_agent_platform.aina.package.tasks import AinaTask, AinaTaskService, TaskStart, result_message_id
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation
from tianzhou_agent_platform.core.base import utc_now
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.memory_repository import installation_record_id
from tianzhou_agent_platform.store.repository import PersistentRepository

REPORT = "mcp_com_example_expenses_monthly_report_"
TASKS = "/aina-host/v1/tasks"
REFRESH = "/aina-host/v1/token/refresh"


async def _finish_jobs(server: Any) -> None:
    await asyncio.gather(*server.JOBS.values())


def _key(installation: AinaInstallation) -> str:
    return installation_record_id(installation.tenant_id, installation.user_id, installation.aina_id)


# --- Long-running tool in a conversation -------------------------------------------------------------------------


def test_long_running_tool_ack_ends_the_turn_and_the_result_is_posted_to_the_conversation() -> None:
    server = _example_server()
    mcp = RecordingMcp({MCP_URL: server.mcp})
    # Only two model responses: a third model call in the first turn would fail the test.
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="a"),
            call_first_tool(prefix=REPORT, call_id="r", arguments='{"month": "2026-09"}'),
        ]
    )
    app = create_app(settings=_settings(), llm=llm, mcp_client=mcp)
    server.HOST_TRANSPORT = httpx.ASGITransport(app=app)
    with TestClient(app) as client:
        _register(client, _example_files())
        client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []})
        expenses = [
            {"id": "e_1", "invocation_id": "x1", "amount": 30.0, "category": "food", "note": "", "day": "2026-09-02"},
            {"id": "e_2", "invocation_id": "x2", "amount": 12.5, "category": "transport", "note": "", "day": "2026-09-03"},
            {"id": "e_3", "invocation_id": "x3", "amount": 99.0, "category": "food", "note": "", "day": "2026-08-30"},
        ]
        _call(client, app.state.repository.put_aina_storage_item, storage_item(
            installation_key=_installation_key(client), aina_id=AINA_ID, key="expenses", value=expenses
        ))

        first = client.post("/chat", json={"message": "Build my September report"}).json()
        model_calls_in_turn = len(llm.calls)
        conversation_id = first["conversation_id"]
        _call(client, _finish_jobs, server)
        conversation = client.get(f"/conversations/{conversation_id}").json()
        tasks = client.get(f"/conversations/{conversation_id}/aina-tasks").json()

        llm.responses.append(assistant("Food was the largest category."))
        client.post("/chat", json={"message": "Which category was largest?", "conversation_id": conversation_id})
        after = client.get(f"/conversations/{conversation_id}").json()

    # The acknowledgement completed the turn without another model call.
    assert first["status"] == "completed"
    assert model_calls_in_turn == 2
    assert '"Spending report · 2026-09" is running in the background' in first["content"]
    # The task ran in the background and reported progress and its result on the Host API.
    (task,) = tasks
    assert task["status"] == "completed" and task["tool_name"] == "monthly_report"
    assert (task["progress"], task["total"]) == (1, 2)
    assert task["structured_content"]["total"] == 42.5
    assert task["structured_content"]["by_category"] == {"food": 30.0, "transport": 12.5}
    # The result was posted to the conversation out of turn.
    posted = conversation["messages"][-1]
    assert posted["role"] == "assistant"
    assert posted["content"].startswith("**Spending report · 2026-09** finished.")
    assert "42.50 CNY across 2 expenses" in posted["content"]
    # The next turn hands it to the model, delimited as third-party output, and archives it only once.
    seen = [message["content"] for message in llm.calls[-1]["messages"] if message["role"] == "assistant"]
    assert any('<aina-task-result aina="com.example.expenses"' in text and "42.5" in text for text in seen)
    assert [message["id"] for message in after["messages"]].count(posted["id"]) == 1


def test_a_result_seeded_from_the_archive_reaches_the_model_delimited() -> None:
    """Without working memory the turn is rebuilt from the archive; the AINA's text must not become the model's own."""
    server = _example_server()
    llm = ScriptedLLM(
        [
            call_first_tool(prefix=ENTRY, call_id="a"),
            call_first_tool(prefix=REPORT, call_id="r", arguments='{"month": "2026-09"}'),
            assistant("Nothing was spent."),
        ]
    )
    app = create_app(settings=_settings(), llm=llm, mcp_client=RecordingMcp({MCP_URL: server.mcp}))
    server.HOST_TRANSPORT = httpx.ASGITransport(app=app)
    with TestClient(app) as client:
        _register(client, _example_files())
        client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []})
        conversation_id = client.post("/chat", json={"message": "Build my September report"}).json()["conversation_id"]
        _call(client, _finish_jobs, server)
        client.get(f"/conversations/{conversation_id}/aina-tasks")  # posts the result
        _call(client, app.state.chat_service.runner.delete_working_memory, conversation_id)
        client.post("/chat", json={"message": "Summarize it", "conversation_id": conversation_id})

    assistant_texts = [m["content"] for m in llm.calls[-1]["messages"] if m["role"] == "assistant" and m["content"]]
    assert any(text.startswith("Background task") and "<aina-task-result" in text for text in assistant_texts)
    assert not any(text.startswith("**Spending report") for text in assistant_texts)


# --- Host API task callbacks -------------------------------------------------------------------------------------


@pytest.fixture
def tasks_host() -> Any:
    server = _example_server()
    app = create_app(settings=_settings(), llm=ScriptedLLM([]), mcp_client=RecordingMcp({MCP_URL: server.mcp}))
    with TestClient(app) as client:
        assert _register(client, _example_files()).status_code == 201
        yield client, app


def _start(
    client: TestClient,
    app: Any,
    installation: AinaInstallation,
    *,
    task_id: str = "t_1",
    invocation_id: str = "call_task",
) -> tuple[AinaTask, str]:
    """Register a running task the way a long-running tool result does; returns it and its conversation id."""
    _call(client, app.state.repository.put_installation, installation)
    conversation_id = client.post("/conversations", json={"title": "Report"}).json()["id"]
    service: AinaTaskService = app.state.aina_tasks
    task = _call(
        client,
        service.register,
        TaskStart(id=task_id, title="Spending report · 2026-09"),
        installation=installation,
        tool_name="monthly_report",
        invocation_id=invocation_id,
        conversation_id=conversation_id,
        workspace_id=None,
    )
    return task, conversation_id


def _messages(client: TestClient, conversation_id: str) -> list[dict[str, Any]]:
    return client.get(f"/conversations/{conversation_id}").json()["messages"]  # type: ignore[no-any-return]


def test_progress_complete_and_fail_update_the_task(tasks_host: Any) -> None:
    client, app = tasks_host
    installation = _installation()
    task, conversation_id = _start(client, app, installation)
    headers = _bearer(_token(app, installation, invocation_id="call_task"))

    progress = client.post(f"{TASKS}/t_1/progress", json={"progress": 3, "total": 10, "message": "Reading"}, headers=headers)
    listed = client.get(f"/conversations/{conversation_id}/aina-tasks").json()
    complete = client.post(
        f"{TASKS}/t_1/complete",
        json={"content": [{"type": "text", "text": "Total 42.50"}], "structuredContent": {"total": 42.5}},
        headers=headers,
    )
    again = client.post(f"{TASKS}/t_1/complete", json={"content": []}, headers=headers)
    late_progress = client.post(f"{TASKS}/t_1/progress", json={"progress": 9}, headers=headers)
    missing = client.post(f"{TASKS}/t_404/progress", json={"progress": 1}, headers=headers)
    messages = _messages(client, conversation_id)

    assert progress.status_code == 200, progress.text
    assert progress.json()["status"] == "running" and progress.json()["message"] == "Reading"
    # The AINA sees only its own task view, never the host's raw ids.
    assert set(progress.json()) == {"id", "title", "status", "progress", "total", "message", "updated_at"}
    assert listed[0]["progress"] == 3 and listed[0]["total"] == 10
    assert complete.status_code == 200 and complete.json()["status"] == "completed"
    assert again.status_code == 409 and late_progress.status_code == 409
    assert missing.status_code == 404
    assert messages[-1]["id"] == result_message_id(task)
    assert messages[-1]["content"] == "**Spending report · 2026-09** finished.\n\nTotal 42.50"

    failing, other_conversation = _start(client, app, installation, task_id="t_2", invocation_id="call_two")
    failed = client.post(f"{TASKS}/t_2/fail", json={"error": "Bank API is down"}, headers=headers)
    stored = _call(client, app.state.repository.get_aina_task, failing.id)
    assert failed.status_code == 200 and failed.json()["status"] == "failed"
    assert stored.error == "Bank API is down" and stored.finished_at is not None
    assert _messages(client, other_conversation)[-1]["content"] == "**Spending report · 2026-09** failed: Bank API is down"


def test_a_result_that_arrives_during_a_turn_is_posted_after_it(tasks_host: Any) -> None:
    client, app = tasks_host
    installation = _installation()
    task, conversation_id = _start(client, app, installation)
    conversations = app.state.conversation_service
    _call(client, conversations.start_run, conversation_id, "trace_running")

    completed = client.post(f"{TASKS}/t_1/complete", json={"content": [{"type": "text", "text": "Done"}]},
                            headers=_bearer(_token(app, installation, invocation_id="call_task")))
    during = _messages(client, conversation_id)
    _call(client, conversations.finish_run, conversation_id, expected_trace_id="trace_running")
    client.get(f"/conversations/{conversation_id}/aina-tasks")  # also what ChatService does when a turn finishes
    after = _messages(client, conversation_id)

    assert completed.status_code == 200
    assert all(message["id"] != result_message_id(task) for message in during)
    assert after[-1]["id"] == result_message_id(task)


def test_a_task_past_its_deadline_fails_and_rejects_callbacks(tasks_host: Any) -> None:
    client, app = tasks_host
    installation = _installation()
    task, conversation_id = _start(client, app, installation)
    _call(client, app.state.repository.update_aina_task, task.id, lambda current: current.model_copy(
        update={"deadline_at": utc_now() - timedelta(seconds=1)}
    ))

    late = client.post(f"{TASKS}/t_1/complete", json={"content": []}, headers=_bearer(_token(app, installation)))
    (listed,) = client.get(f"/conversations/{conversation_id}/aina-tasks").json()

    assert late.status_code == 409
    assert listed["status"] == "failed" and listed["error"] == "The task did not finish in time"
    assert _messages(client, conversation_id)[-1]["content"].endswith("failed: The task did not finish in time")


def test_another_installation_cannot_touch_the_task(tasks_host: Any) -> None:
    client, app = tasks_host
    alice = _installation(user_id="alice")
    task, conversation_id = _start(client, app, alice)
    second_package = _example_files()
    from tests.test_aina_package import _set, _with_manifest

    assert _register(client, _with_manifest(second_package, _set("aina.id", "com.example.budget"))).status_code == 201
    others = [
        _installation(user_id="bob"),
        _installation(user_id="alice", tenant_id="t2"),
        _installation("com.example.budget", user_id="alice"),
    ]
    for other in others:
        _call(client, app.state.repository.put_installation, other)
        headers = _bearer(_token(app, other, invocation_id="call_task"))
        for action, body in (("progress", {"progress": 1}), ("complete", {"content": []}), ("fail", {"error": "x"})):
            assert client.post(f"{TASKS}/t_1/{action}", json=body, headers=headers).status_code == 404
        assert client.post(REFRESH, json={"task_id": "t_1"}, headers=headers).status_code == 404

    unchanged = _call(client, app.state.repository.get_aina_task, task.id)
    assert unchanged.status == "running" and unchanged.progress is None
    assert all(not message["id"].startswith("msg_aina_task_") for message in _messages(client, conversation_id))


def test_an_expired_task_token_is_refreshed_only_while_the_task_is_live(tasks_host: Any) -> None:
    client, app = tasks_host
    installation = _installation()
    _start(client, app, installation)
    service = app.state.aina_packages
    live = _token(app, installation, invocation_id="call_task")
    reference = service.tokens.verify(live).reference
    claims = {key: value for key, value in service.tokens.verify(live).claims.items()
              if key not in {"iss", "aud", "iat", "exp", "jti", "ref"}}
    expired = service.tokens.mint(reference=reference, claims=claims, ttl_seconds=-1)
    other_invocation = service.tokens.mint(
        reference=reference, claims={**claims, "invocation_id": "call_other"}, ttl_seconds=-1
    )

    rejected = client.post(f"{TASKS}/t_1/progress", json={"progress": 1}, headers=_bearer(expired))
    tampered = client.post(REFRESH, json={"task_id": "t_1"}, headers=_bearer(expired[:-4] + "AAAA"))
    wrong_invocation = client.post(REFRESH, json={"task_id": "t_1"}, headers=_bearer(other_invocation))
    unknown = client.post(REFRESH, json={"task_id": "t_404"}, headers=_bearer(expired))
    refreshed = client.post(REFRESH, json={"task_id": "t_1"}, headers=_bearer(expired))
    renewed = _bearer(refreshed.json()["token"])
    progressed = client.post(f"{TASKS}/t_1/progress", json={"progress": 1}, headers=renewed)
    # An expired token is not accepted anywhere else.
    storage = client.get("/aina-host/v1/storage/kv", headers=_bearer(expired))
    completed = client.post(f"{TASKS}/t_1/complete", json={"content": []}, headers=renewed)
    after_completion = client.post(REFRESH, json={"task_id": "t_1"}, headers=_bearer(expired))

    assert rejected.status_code == 401
    assert tampered.status_code == 401
    assert wrong_invocation.status_code == 401
    assert unknown.status_code == 404
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["expires_in"] == 600
    new_claims = service.tokens.verify(refreshed.json()["token"]).claims
    assert new_claims["invocation_id"] == "call_task" and new_claims["sub"] == claims["sub"]
    assert new_claims["scopes"] == ["storage"]
    assert progressed.status_code == 200
    assert storage.status_code == 401
    assert completed.status_code == 200
    assert after_completion.status_code == 409


def test_refresh_drops_scopes_revoked_since_the_token_was_minted(tasks_host: Any) -> None:
    client, app = tasks_host
    installation = _installation()
    _start(client, app, installation)
    token = _token(app, installation, invocation_id="call_task")
    _call(client, app.state.repository.put_installation, installation.model_copy(update={"granted_permissions": []}))

    refreshed = client.post(REFRESH, json={"task_id": "t_1"}, headers=_bearer(token))

    assert refreshed.status_code == 200
    assert app.state.aina_packages.tokens.verify(refreshed.json()["token"]).claims["scopes"] == []


# --- SQL persistence ----------------------------------------------------------------------------------------------


class _SqlOnly:
    def __init__(self) -> None:
        self.session_factory = sqlite_session_factory()


async def test_sql_repository_persists_aina_tasks() -> None:
    stores = StorageStores(mysql=_SqlOnly(), redis=None, nas=None)  # type: ignore[arg-type]
    first, second = PersistentRepository(stores), PersistentRepository(stores)
    installation = _installation()
    now = utc_now()
    task = AinaTask(
        id="a" * 64,
        external_id="t_1",
        installation_key=_key(installation),
        aina_id=AINA_ID,
        tenant_id="t1",
        user_id="alice",
        conversation_id="conv_1",
        tool_name="monthly_report",
        invocation_id="call_1",
        title="Report",
        deadline_at=now + timedelta(hours=1),
    )
    await first.create_aina_task(task)

    with pytest.raises(Exception) as duplicate:
        await second.create_aina_task(task)
    updated = await second.update_aina_task(
        task.id, lambda current: current.model_copy(update={"status": "completed", "structured_content": {"n": 1}})
    )
    loaded = await first.get_aina_task(task.id)

    assert getattr(duplicate.value, "code", None) == "CONFLICT"
    assert updated is not None and updated.status == "completed"
    assert loaded is not None and loaded.structured_content == {"n": 1} and loaded.deadline_at == task.deadline_at
    assert [item.id for item in await second.list_aina_tasks("conv_1")] == [task.id]
    assert await second.list_aina_tasks("conv_other") == []
    assert await first.update_aina_task("b" * 64, lambda current: current) is None
