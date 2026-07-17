import hashlib
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.errors import StorageValidationError
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
                description_contains="完整内容",
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


def test_document_aina_updates_only_the_requested_markdown_section(tmp_path: Path) -> None:
    original = (
        "# 使用手册\n\n"
        "开场说明。\n\n"
        "## 第一节\n\n"
        "UNCHANGED_FIRST_SECTION\n\n"
        "## 第四节\n\n"
        "需要翻译的原文。\n\n"
        "### 示例\n\n"
        "```markdown\n"
        "## 代码块内不是目录标题\n"
        "```\n\n"
        "## 第五节\n\n"
        "UNCHANGED_FIFTH_SECTION\n"
    )
    revision = hashlib.sha256(original.encode("utf-8")).hexdigest()
    replacement = "## 第四节\n\nTranslated source text.\n\n### Example\n\nUpdated example."
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="builtin_document_outline_",
                arguments='{"name":"manual.md"}',
                call_id="call_outline",
            ),
            call_first_tool(
                prefix="builtin_document_read_section_",
                arguments='{"name":"manual.md","heading":"第四节"}',
                call_id="call_read_section",
            ),
            call_first_tool(
                prefix="builtin_document_update_section_",
                arguments=json.dumps(
                    {
                        "name": "manual.md",
                        "heading": "第四节",
                        "section_content": replacement,
                        "expected_revision": revision,
                    },
                    ensure_ascii=False,
                ),
                call_id="call_update_section",
            ),
            assistant("第四节已更新。"),
        ]
    )

    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "manual", "content": original})
        response = client.post(
            "/chat",
            json={"message": "把第四节翻译成英文", "capability": "aina:unibot-documents"},
        )
        document = client.get("/documents/manual.md")
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    updated_content = document.json()["content"]
    assert response.status_code == 200
    assert "UNCHANGED_FIRST_SECTION" in updated_content
    assert "UNCHANGED_FIFTH_SECTION" in updated_content
    assert replacement in updated_content
    assert "需要翻译的原文" not in updated_content
    assert "代码块内不是目录标题" not in json.dumps(llm.calls[1]["messages"], ensure_ascii=False)
    assert "UNCHANGED_FIRST_SECTION" not in json.dumps(llm.calls, ensure_ascii=False)
    assert "UNCHANGED_FIFTH_SECTION" not in json.dumps(llm.calls, ensure_ascii=False)
    assert any(
        event["kind"] == "builtin.completed" and event["target_id"] == "document.update_section"
        for event in trace.json()["events"]
    )
    update_request = next(
        event
        for event in trace.json()["events"]
        if event["kind"] == "builtin.requested" and event["target_id"] == "document.update_section"
    )
    assert "UNCHANGED_FIRST_SECTION" not in json.dumps(update_request["details"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_document_section_update_rejects_a_stale_revision(tmp_path: Path) -> None:
    service = DocumentService(NasStore(tmp_path))
    original = "# Notes\n\n## Target\n\nOld text.\n\n## Keep\n\nKeep text.\n"
    await service.create_document("notes", original, user_id="alice", tenant_id="team-a")
    section = await service.get_section(
        "notes.md",
        "Target",
        1,
        user_id="alice",
        tenant_id="team-a",
    )
    current = original.replace("Keep text.", "Newer text from another edit.")
    await service.update_document("notes.md", current, user_id="alice", tenant_id="team-a")

    with pytest.raises(StorageValidationError, match="revision changed"):
        await service.update_section(
            "notes.md",
            "Target",
            1,
            "## Target\n\nStale replacement.",
            section.revision,
            user_id="alice",
            tenant_id="team-a",
        )

    latest = await service.get_document("notes.md", user_id="alice", tenant_id="team-a")
    assert latest.content == current
