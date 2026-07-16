from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.nas.filesystem import NasStore
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
    )


def _app(tmp_path: Path, llm: ScriptedLLM | None = None) -> FastAPI:
    return create_app(
        settings=_settings(),
        llm=llm or ScriptedLLM([]),
        document_service=DocumentService(NasStore(tmp_path)),
    )


def test_document_aina_opens_editor_and_crud_persists_to_nas(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        ainas = client.get("/ainas")
        opened = client.post("/ainas/unibot-documents/open", json={})
        created = client.post("/documents", json={"name": "notes", "content": "# First"})
        listed = client.get("/documents")
        loaded = client.get("/documents/notes.md")
        updated = client.put("/documents/notes.md", json={"content": "# Updated"})
        renamed = client.post("/documents/notes.md/rename", json={"new_name": "project"})

    assert {item["manifest"]["aina"]["id"] for item in ainas.json()} >= {
        "unibot-assistant",
        "unibot-memory",
        "unibot-documents",
    }
    assert opened.json()["main_widget"]["kind"] == "document"
    assert created.status_code == 201
    assert created.json()["name"] == "notes.md"
    assert listed.json()["items"][0]["name"] == "notes.md"
    assert loaded.json()["content"] == "# First"
    assert updated.json()["content"] == "# Updated"
    assert renamed.json()["name"] == "project.md"
    assert (tmp_path / "documents" / "t-default" / "u-anonymous" / "project.md").read_text(
        encoding="utf-8"
    ) == "# Updated"
    assert not (tmp_path / "documents" / "t-default" / "u-anonymous" / "notes.md").exists()


def test_document_api_isolates_actors_and_rejects_non_markdown_names(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        created = client.post(
            "/documents",
            json={"name": "private", "content": "secret", "user_id": "alice", "tenant_id": "team-a"},
        )
        other_user = client.get("/documents", params={"user_id": "bob", "tenant_id": "team-a"})
        invalid = client.post("/documents", json={"name": "notes.txt", "content": "not markdown"})

    assert created.status_code == 201
    assert other_user.json()["total"] == 0
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "INVALID_REQUEST"


def test_document_aina_chat_creates_markdown_file(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="builtin_document_create_",
                arguments='{"name":"plan","content":"# Plan\\n\\n- Build"}',
            ),
            assistant("Created plan.md."),
        ]
    )
    with TestClient(_app(tmp_path, llm)) as client:
        response = client.post(
            "/chat",
            json={"message": "Create plan.md", "capability": "aina:unibot-documents"},
        )
        document = client.get("/documents/plan.md")
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    assert response.status_code == 200
    assert response.json()["content"] == "Created plan.md."
    assert document.json()["content"] == "# Plan\n\n- Build"
    assert all(item["function"]["name"].startswith("builtin_document_") for item in llm.calls[0]["tools"])
    assert any(
        event["kind"] == "builtin.completed" and event["target_id"] == "document.create"
        for event in trace.json()["events"]
    )


def test_document_delete_requires_approval(tmp_path: Path) -> None:
    llm = ScriptedLLM([])
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "temporary", "content": "remove me"})
        llm.responses.extend(
            [
                call_first_tool(
                    prefix="builtin_document_delete_",
                    arguments='{"name":"temporary.md"}',
                ),
                assistant("Deleted temporary.md."),
            ]
        )
        pending = client.post(
            "/chat",
            json={"message": "Delete temporary.md", "capability": "aina:unibot-documents"},
        )
        existing = client.get("/documents/temporary.md")
        confirmed = client.post(f"/approvals/{pending.json()['approval']['id']}/confirm", json={})
        missing = client.get("/documents/temporary.md")

    assert pending.json()["status"] == "approval_required"
    assert existing.status_code == 200
    assert confirmed.json()["status"] == "completed"
    assert missing.status_code == 404


def test_missing_document_is_returned_to_model_for_list_and_retry(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="builtin_document_read_",
                arguments='{"name":"stale.md"}',
            ),
            call_first_tool(prefix="builtin_document_list_", arguments="{}"),
            assistant("The current document is current.md."),
        ]
    )
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "current", "content": "# Current"})
        response = client.post(
            "/chat",
            json={"message": "Read the document", "capability": "aina:unibot-documents"},
        )
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    tool_error = next(item for item in llm.calls[1]["messages"] if item["role"] == "tool")
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["content"] == "The current document is current.md."
    assert "Call document.list" in tool_error["content"]
    assert any(
        event["kind"] == "builtin.failed" and event["target_id"] == "document.read"
        for event in trace.json()["events"]
    )
    assert any(
        event["kind"] == "builtin.completed" and event["target_id"] == "document.list"
        for event in trace.json()["events"]
    )
