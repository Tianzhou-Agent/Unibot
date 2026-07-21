import time
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
        created = client.post("/documents", json={"name": "notes", "content": "# First\n\nBody."})
        listed = client.get("/documents")
        loaded = client.get("/documents/notes.md")
        section = client.get("/documents/notes.md/sections", params={"heading": "First"}).json()
        full_update = client.put("/documents/notes.md", json={"content": "# Replaced"})
        updated = client.put(
            "/documents/notes.md/sections",
            json={
                "heading": "First",
                "section_content": "# Updated\n\nBody.",
                "expected_revision": section["revision"],
            },
        )
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
    assert loaded.json()["content"] == "# First\n\nBody."
    assert full_update.status_code == 405
    assert updated.status_code == 200
    assert renamed.json()["name"] == "project.md"
    assert (tmp_path / "documents" / "t-default" / "u-anonymous" / "project.md").read_text(
        encoding="utf-8"
    ) == "# Updated\n\nBody."
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
    assert any(
        item["function"]["name"].startswith("builtin_document_create_")
        for item in llm.calls[0]["tools"]
    )
    assert any(
        event["kind"] == "builtin.completed" and event["target_id"] == "document.create"
        for event in trace.json()["events"]
    )


def test_document_aina_chat_creates_reviewed_edit_task_without_changing_source(tmp_path: Path) -> None:
    original = "# Guide\n\n## Background\n\nOld background.\n\n## Conclusion\n\nOld conclusion.\n"
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="builtin_document_outline_",
                arguments='{"name":"guide.md"}',
            ),
            call_first_tool(
                prefix="builtin_document_edit_task_create_",
                arguments=(
                    '{"name":"guide.md","description":"Rewrite the background",'
                    '"sections":[{"heading":"Background","occurrence":1}]}'
                ),
            ),
            call_first_tool(
                prefix="submit_document_section_draft",
                arguments='{"section_content":"## Background\\n\\nAI draft."}',
            ),
        ]
    )

    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "guide", "content": original})
        response = client.post(
            "/chat",
            json={"message": "Rewrite the Background section", "capability": "aina:unibot-documents"},
        )
        tasks = client.get("/documents/guide.md/edit-tasks").json()["items"]
        task_id = tasks[0]["id"]
        for _ in range(100):
            task = client.get(f"/document-edit-tasks/{task_id}").json()
            if task["status"] in {"reviewing", "failed"}:
                break
            time.sleep(0.05)
        document = client.get("/documents/guide.md").json()
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert "已创建" in response.json()["content"]
    assert "后台处理" in response.json()["content"]
    assert task["status"] == "reviewing"
    assert task["sections"][0]["draft_content"] == "## Background\n\nAI draft."
    assert document["content"] == original
    assert any(
        event["kind"] == "builtin.completed" and event["target_id"] == "document.edit_task.create"
        for event in trace["events"]
    )
    assert sum(event["kind"] == "model.completed" for event in trace["events"]) == 2


def test_document_aina_merge_task_requires_confirmation(tmp_path: Path) -> None:
    original = "# Guide\n\n## Background\n\nOld background.\n"
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="submit_document_section_draft",
                arguments='{"section_content":"## Background\\n\\nReviewed draft."}',
            )
        ]
    )

    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "guide", "content": original})
        task_id = client.post(
            "/documents/guide.md/edit-tasks",
            json={"description": "Rewrite background", "sections": [{"heading": "Background"}]},
        ).json()["id"]
        for _ in range(100):
            task = client.get(f"/document-edit-tasks/{task_id}").json()
            if task["status"] in {"reviewing", "failed"}:
                break
            time.sleep(0.05)
        llm.responses.extend(
            [
                call_first_tool(
                    prefix="builtin_document_edit_task_merge_",
                    arguments=f'{{"task_id":"{task_id}"}}',
                ),
                assistant("The reviewed task was merged."),
            ]
        )
        pending = client.post(
            "/chat",
            json={"message": "Merge the reviewed task", "capability": "aina:unibot-documents"},
        )
        before_confirm = client.get("/documents/guide.md").json()["content"]
        confirmed = client.post(f"/approvals/{pending.json()['approval']['id']}/confirm", json={})
        after_confirm = client.get("/documents/guide.md").json()["content"]

    assert task["status"] == "reviewing"
    assert pending.json()["status"] == "approval_required"
    assert before_confirm == original
    assert confirmed.json()["status"] == "completed"
    assert "Reviewed draft." in after_confirm
    assert "Old background." not in after_confirm


def test_document_aina_keeps_global_memory_update_available(tmp_path: Path) -> None:
    llm = ScriptedLLM([])
    with TestClient(_app(tmp_path, llm)) as client:
        memory = client.post(
            "/memories",
            json={"content": "The user is an engineer", "category": "fact"},
        ).json()
        llm.responses.extend(
            [
                call_first_tool(
                    prefix="builtin_memory_update_",
                    arguments=(
                        f'{{"memory_id":"{memory["id"]}",'
                        '"content":"The user is a software engineer","category":"fact"}'
                    ),
                ),
                assistant("I updated your occupation."),
            ]
        )
        response = client.post(
            "/chat",
            json={
                "message": "I am a software engineer",
                "capability": "aina:unibot-documents",
            },
        )
        memories = client.get("/memories")
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    assert response.status_code == 200
    assert memories.json()["total"] == 1
    assert memories.json()["items"][0]["id"] == memory["id"]
    assert memories.json()["items"][0]["content"] == "The user is a software engineer"
    assert any(
        item["function"]["name"].startswith("builtin_memory_update_")
        for item in llm.calls[0]["tools"]
    )
    assert any(
        event["kind"] == "builtin.completed" and event["target_id"] == "memory.update"
        for event in trace.json()["events"]
    )


def test_conversation_alternates_preferred_ainas_without_router_model(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            assistant("The document AINA handled this turn."),
            assistant("The memory AINA handled this turn."),
        ]
    )
    with TestClient(_app(tmp_path, llm)) as client:
        conversation = client.post("/conversations", json={"title": "Multi AINA"}).json()
        first = client.post(
            "/chat",
            json={
                "message": "Work on the document",
                "conversation_id": conversation["id"],
                "preferred_aina_id": "unibot-documents",
            },
        )
        second = client.post(
            "/chat",
            json={
                "message": "Work with memory instead",
                "conversation_id": conversation["id"],
                "preferred_aina_id": "unibot-memory",
            },
        )
        updated = client.get(f"/conversations/{conversation['id']}").json()
        trace = client.get(f"/traces/{second.json()['trace_id']}").json()

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(llm.calls) == 2
    assert any(
        item["function"]["name"].startswith("builtin_document_")
        for item in llm.calls[0]["tools"]
    )
    assert all(
        item["function"]["name"].startswith("builtin_memory_")
        for item in llm.calls[1]["tools"]
    )
    assert updated["active_aina_ids"] == ["unibot-documents", "unibot-memory"]
    assert updated["primary_aina_id"] == "unibot-documents"
    assert updated["last_aina_id"] == "unibot-memory"
    resolution = next(event for event in trace["events"] if event["kind"] == "routing.scope.resolved")
    assert resolution["target_id"] == "unibot-memory"
    assert resolution["details"]["source"] == "preferred_aina"
    assert resolution["details"]["router_model_called"] is False


def test_short_follow_up_reuses_last_aina_without_router_model(tmp_path: Path) -> None:
    llm = ScriptedLLM([assistant("Started."), assistant("Continued.")])
    with TestClient(_app(tmp_path, llm)) as client:
        conversation = client.post("/conversations", json={"title": "Sticky AINA"}).json()
        first = client.post(
            "/chat",
            json={
                "message": "Start editing the document",
                "conversation_id": conversation["id"],
                "preferred_aina_id": "unibot-documents",
            },
        )
        second = client.post(
            "/chat",
            json={"message": "Continue", "conversation_id": conversation["id"]},
        )
        trace = client.get(f"/traces/{second.json()['trace_id']}").json()

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(llm.calls) == 2
    resolution = next(event for event in trace["events"] if event["kind"] == "routing.scope.resolved")
    assert resolution["target_id"] == "unibot-documents"
    assert resolution["details"]["source"] == "sticky_aina"
    assert resolution["details"]["router_model_called"] is False


def test_single_active_primary_aina_bypasses_router_model(tmp_path: Path) -> None:
    llm = ScriptedLLM([assistant("Handled by the primary document AINA.")])
    with TestClient(_app(tmp_path, llm)) as client:
        conversation = client.post(
            "/conversations",
            json={
                "title": "Primary AINA",
                "active_aina_ids": ["unibot-documents"],
                "primary_aina_id": "unibot-documents",
            },
        ).json()
        response = client.post(
            "/chat",
            json={"message": "Edit the introduction", "conversation_id": conversation["id"]},
        )
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    assert response.status_code == 200
    assert len(llm.calls) == 1
    resolution = next(event for event in trace["events"] if event["kind"] == "routing.scope.resolved")
    assert resolution["target_id"] == "unibot-documents"
    assert resolution["details"]["source"] == "primary_aina"
    assert resolution["details"]["router_model_called"] is False


def test_ambiguous_turn_routes_across_active_ainas_with_model(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="aina_unibot-memory_",
                arguments='{"input":"Use my profile"}',
            ),
            assistant("The memory AINA handled the ambiguous turn."),
        ]
    )
    with TestClient(_app(tmp_path, llm)) as client:
        conversation = client.post(
            "/conversations",
            json={
                "title": "Router fallback",
                "active_aina_ids": ["unibot-documents", "unibot-memory"],
                "primary_aina_id": "unibot-documents",
                "last_aina_id": "unibot-documents",
            },
        ).json()
        response = client.post(
            "/chat",
            json={"message": "Use my profile", "conversation_id": conversation["id"]},
        )
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    assert response.status_code == 200
    assert len(llm.calls) == 2
    assert all(item["function"]["name"].startswith("aina_") for item in llm.calls[0]["tools"])
    assert len(llm.calls[0]["tools"]) == 2
    resolution = next(event for event in trace["events"] if event["kind"] == "routing.scope.resolved")
    assert resolution["target_id"] == "unibot-memory"
    assert resolution["details"]["source"] == "model_router"
    assert resolution["details"]["router_model_called"] is True


def test_opening_aina_binds_it_as_conversation_primary(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        conversation = client.post("/conversations", json={"title": "Canvas binding"}).json()
        opened = client.post(
            "/ainas/unibot-documents/open",
            json={"conversation_id": conversation["id"]},
        )
        updated = client.get(f"/conversations/{conversation['id']}").json()

    assert opened.status_code == 200
    assert updated["active_aina_ids"] == ["unibot-documents"]
    assert updated["primary_aina_id"] == "unibot-documents"
    assert updated["last_aina_id"] is None


def test_document_browse_returns_interactive_chapter_widget(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="builtin_document_browse_",
                arguments='{"name":"guide.md"}',
            ),
            assistant("已加载章节导航，请在组件中选择要查看的章节。"),
        ]
    )
    content = "# 使用指南\n\n## 入门\n\n欢迎使用。\n\n## 进阶\n\n高级内容。\n"
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "guide", "content": content})
        response = client.post(
            "/chat",
            json={"message": "查看当前文档章节", "capability": "aina:unibot-documents"},
        )
        section = client.get(
            "/documents/guide.md/sections",
            params={"heading": "进阶", "occurrence": 1},
        )
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    widget = response.json()["widgets"][0]
    tool_result = next(message for message in llm.calls[1]["messages"] if message["role"] == "tool")
    assert response.status_code == 200
    assert response.json()["content"] == "已加载章节导航，请在组件中选择要查看的章节。"
    assert widget["kind"] == "document_outline"
    assert widget["document_name"] == "guide.md"
    assert [item["heading"] for item in widget["sections"]] == ["使用指南", "入门", "进阶"]
    assert "interactive_document_outline_widget" in tool_result["content"]
    assert "高级内容" not in tool_result["content"]
    assert section.status_code == 200
    assert section.json()["content"] == "## 进阶\n\n高级内容。\n"
    browse_event = next(
        event
        for event in trace.json()["events"]
        if event["kind"] == "builtin.completed" and event["target_id"] == "document.browse"
    )
    assert browse_event["details"]["widgets"] == [
        {"id": widget["id"], "kind": "document_outline"}
    ]


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


def test_document_aina_directly_updates_section_in_edit_mode(tmp_path: Path) -> None:
    original = (
        "# Manual\n\n"
        "## First\n\n"
        "UNCHANGED_FIRST_SECTION\n\n"
        "## Fourth\n\n"
        "Text to translate.\n\n"
        "## Fifth\n\n"
        "UNCHANGED_FIFTH_SECTION\n"
    )
    llm = ScriptedLLM([])

    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "manual", "content": original})
        revision = client.get(
            "/documents/manual.md/sections",
            params={"heading": "Fourth"},
        ).json()["revision"]
        llm.responses.extend(
            [
                call_first_tool(
                    prefix="builtin_document_outline_",
                    arguments='{"name":"manual.md"}',
                    call_id="call_outline",
                ),
                call_first_tool(
                    prefix="builtin_document_read_section_",
                    arguments='{"name":"manual.md","heading":"Fourth"}',
                    call_id="call_read_section",
                ),
                call_first_tool(
                    prefix="builtin_document_update_section_",
                    arguments=(
                        '{"name":"manual.md","heading":"Fourth",'
                        '"section_content":"## Fourth\\n\\nTranslated directly.",'
                        f'"expected_revision":"{revision}"}}'
                    ),
                    call_id="call_update_section",
                ),
                assistant("Updated the section directly."),
            ]
        )
        response = client.post(
            "/chat",
            json={
                "message": "Directly edit the Fourth section and save it now",
                "capability": "aina:unibot-documents",
            },
        )
        document = client.get("/documents/manual.md")
        trace = client.get(f"/traces/{response.json()['trace_id']}")

    assert response.status_code == 200
    assert "Translated directly." in document.json()["content"]
    assert "UNCHANGED_FIRST_SECTION" in document.json()["content"]
    assert "UNCHANGED_FIFTH_SECTION" in document.json()["content"]
    assert any(
        event["kind"] == "builtin.completed" and event["target_id"] == "document.update_section"
        for event in trace.json()["events"]
    )


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
