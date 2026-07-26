import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.llm import LLMResult
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


def test_document_section_changes_update_multiple_units_atomically(tmp_path: Path) -> None:
    content = "Preface.\n\n# Guide\n\nRoot body.\n\n## One\n\nOld one.\n\n## Two\n\nOld two.\n"
    with TestClient(_app(tmp_path)) as client:
        client.post("/documents", json={"name": "guide", "content": content})
        revision = client.get("/documents/guide.md/outline").json()["revision"]
        updated = client.put(
            "/documents/guide.md/section-changes",
            json={
                "expected_revision": revision,
                "content": (
                    "Updated preface.\n\n# Handbook\n\nUpdated root.\n\n"
                    "## One\n\nUpdated one.\n\n### Detail\n\nNew detail.\n\n"
                    "## Two\n\nOld two.\n"
                ),
            },
        )
        stale = client.put(
            "/documents/guide.md/section-changes",
            json={
                "expected_revision": revision,
                "content": "## Two\n\nStale update.\n",
            },
        )
        loaded = client.get("/documents/guide.md").json()

    assert updated.status_code == 200
    assert updated.json()["updated_sections"] == ["Handbook", "One", "Detail", "Two"]
    assert stale.status_code == 422
    assert loaded["content"] == (
        "Updated preface.\n\n# Handbook\n\nUpdated root.\n\n"
        "## One\n\nUpdated one.\n\n### Detail\n\nNew detail.\n\n"
        "## Two\n\nOld two.\n"
    )


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


def test_document_folders_support_nested_documents_and_safe_moves(tmp_path: Path) -> None:
    with TestClient(_app(tmp_path)) as client:
        root = client.post("/documents/folders", json={"path": "Projects"})
        nested = client.post("/documents/folders", json={"path": "Projects/2026"})
        created = client.post(
            "/documents",
            json={"name": "Projects/2026/plan", "content": "# Plan\n\n## Scope\n\nDraft."},
        )
        tree = client.get("/documents/tree").json()
        loaded = client.get("/documents/Projects/2026/plan.md")
        moved = client.post(
            "/documents/folders/Projects/2026/rename",
            json={"new_path": "Projects/Archive"},
        )
        moved_document = client.get("/documents/Projects/Archive/plan.md")
        non_empty_delete = client.delete("/documents/folders/Projects/Archive")
        traversal = client.post("/documents", json={"name": "../outside", "content": "unsafe"})
        client.delete("/documents/Projects/Archive/plan.md")
        deleted_nested = client.delete("/documents/folders/Projects/Archive")
        deleted_root = client.delete("/documents/folders/Projects")

    assert root.status_code == 201
    assert nested.status_code == 201
    assert created.status_code == 201
    assert created.json()["name"] == "Projects/2026/plan.md"
    assert [item["path"] for item in tree["folders"]] == ["Projects", "Projects/2026"]
    assert [item["name"] for item in tree["documents"]] == ["Projects/2026/plan.md"]
    assert loaded.json()["content"].endswith("Draft.")
    assert moved.json()["path"] == "Projects/Archive"
    assert moved_document.status_code == 200
    assert non_empty_delete.status_code == 422
    assert traversal.status_code == 422
    assert deleted_nested.status_code == 204
    assert deleted_root.status_code == 204


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


def test_document_lookup_activates_scope_then_searches_without_opening_canvas(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(prefix="aina_unibot-documents_", arguments="{}"),
            call_first_tool(
                prefix="builtin_document_search_",
                arguments='{"query":"古诗"}',
            ),
            assistant("找到了古诗选.md。"),
        ]
    )
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "古诗选", "content": "# 唐诗\n\n静夜思。"})
        client.post("/documents", json={"name": "收藏", "content": "# 摘录\n\n这里保存了一首古诗。"})
        client.post("/documents", json={"name": "旅行", "content": "# 行程\n\n参观博物馆。"})
        response = client.post("/chat", json={"message": "查看古诗文档"})
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    entry = next(
        item
        for item in llm.calls[0]["tools"]
        if item["function"]["name"].startswith("aina_unibot-documents_")
    )
    assert entry["function"]["parameters"] == {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }
    assert "takes no arguments" in entry["function"]["description"]
    assert "document.edit_task" not in entry["function"]["description"]
    assert any(
        item["function"]["name"].startswith("builtin_document_search_")
        for item in llm.calls[1]["tools"]
    )
    tool_result = next(
        item
        for item in llm.calls[2]["messages"]
        if item.get("name", "").startswith("builtin_document_search_")
    )
    assert "古诗选.md" in tool_result["content"]
    assert "收藏.md" in tool_result["content"]
    assert "这里保存了一首古诗" in tool_result["content"]
    assert "旅行.md" not in tool_result["content"]
    assert not any(event["target_id"] == "open_aina" for event in trace["events"])
    assert any(
        event["kind"] == "builtin.completed" and event["target_id"] == "document.search"
        for event in trace["events"]
    )


def test_wrong_scope_document_call_recovers_by_activating_owner_aina(tmp_path: Path) -> None:
    def call_unadvertised_document_tool(**_: object) -> LLMResult:
        return LLMResult(
            message={
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_wrong_scope",
                        "type": "function",
                        "function": {
                            "name": "document.create",
                            "arguments": '{"name":"recovered","content":"# Recovered"}',
                        },
                    }
                ],
            },
            finish_reason="tool_calls",
        )

    llm = ScriptedLLM(
        [
            call_first_tool(prefix="aina_unibot-code-runner_", call_id="call_code_scope"),
            call_unadvertised_document_tool,
            call_first_tool(prefix="aina_unibot-documents_", call_id="call_document_scope"),
            call_first_tool(
                prefix="builtin_document_create_",
                arguments='{"name":"recovered","content":"# Recovered"}',
                call_id="call_create_document",
            ),
            assistant("The document was created."),
        ]
    )
    with TestClient(_app(tmp_path, llm)) as client:
        response = client.post("/chat", json={"message": "Run code, then create a document"})
        document = client.get("/documents/recovered.md")
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert document.status_code == 200
    assert document.json()["content"] == "# Recovered"
    assert any(
        item["function"]["name"].startswith("aina_unibot-documents_")
        for item in llm.calls[1]["tools"]
    )
    recovery_context = json.dumps(llm.calls[2]["messages"], ensure_ascii=False)
    assert "CAPABILITY_SCOPE_REQUIRED" in recovery_context
    assert "owner_aina_id" in recovery_context
    assert "unibot-documents" in recovery_context
    assert any(
        event["kind"] == "builtin.failed"
        and event["target_id"] == "document.create"
        and event["details"]["code"] == "CAPABILITY_SCOPE_REQUIRED"
        for event in trace["events"]
    )


def test_historical_document_tool_calls_are_non_executable_outside_scope(tmp_path: Path) -> None:
    def assert_scoped_history(*, messages: list[dict[str, Any]], **_: object) -> LLMResult:
        serialized = json.dumps(messages, ensure_ascii=False)
        assert "<historical-capability-result>" in serialized
        assert not any(
            message.get("role") == "tool"
            and str(message.get("name", "")).startswith("builtin_document_create_")
            for message in messages
        )
        assert not any(
            any(
                str((call.get("function") or {}).get("name", "")).startswith("builtin_document_create_")
                for call in message.get("tool_calls", [])
            )
            for message in messages
            if isinstance(message.get("tool_calls"), list)
        )
        return assistant("The earlier document result remains available as history.")

    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="builtin_document_create_",
                arguments='{"name":"history","content":"# History"}',
                call_id="call_history_document",
            ),
            assistant("Created."),
            assert_scoped_history,
        ]
    )
    with TestClient(_app(tmp_path, llm)) as client:
        conversation = client.post("/conversations", json={"title": "History"}).json()
        first = client.post(
            "/chat",
            json={
                "message": "Create a document",
                "conversation_id": conversation["id"],
                "preferred_aina_id": "unibot-documents",
            },
        )
        second = client.post(
            "/chat",
            json={"message": "What happened earlier?", "conversation_id": conversation["id"]},
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "completed"


def test_pydantic_argument_failure_is_returned_to_model_instead_of_http_500(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="builtin_document_edit_task_create_",
                arguments=(
                    '{"name":"guide.md","description":"Rewrite the section",'
                    '"sections":[{"heading":"   ","occurrence":1}]}'
                ),
                call_id="call_invalid_section",
            ),
            assistant("The section heading was invalid."),
        ]
    )
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "guide", "content": "# Guide\n\nBody."})
        response = client.post(
            "/chat",
            json={
                "message": "Rewrite the section",
                "preferred_aina_id": "unibot-documents",
            },
        )
        trace = client.get(f"/traces/{response.json()['trace_id']}").json()

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    tool_result = next(
        message
        for message in llm.calls[1]["messages"]
        if message.get("tool_call_id") == "call_invalid_section"
    )
    assert json.loads(tool_result["content"])["error"]["code"] == "INVALID_REQUEST"
    assert any(
        event["kind"] == "builtin.failed"
        and event["target_id"] == "document.edit_task.create"
        and event["details"]["code"] == "INVALID_REQUEST"
        for event in trace["events"]
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


def test_document_aina_merge_section_requires_confirmation(tmp_path: Path) -> None:
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
        section_id = task["sections"][0]["id"]
        llm.responses.extend(
            [
                call_first_tool(
                    prefix="builtin_document_edit_task_merge_section_",
                    arguments=f'{{"task_id":"{task_id}","section_id":"{section_id}"}}',
                ),
                assistant("The reviewed section was merged."),
            ]
        )
        pending = client.post(
            "/chat",
            json={"message": "Merge the reviewed section", "capability": "aina:unibot-documents"},
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


def test_preferred_document_aina_receives_transient_ui_context(tmp_path: Path) -> None:
    llm = ScriptedLLM([assistant("I will update the selected draft section.")])
    with TestClient(_app(tmp_path, llm)) as client:
        response = client.post(
            "/chat",
            json={
                "message": "继续修改当前章节",
                "preferred_aina_id": "unibot-documents",
                "ui_context": "任务 ID：task-1\n章节 ID：section-1\n当前草稿版本：2",
            },
        )
        conversation = client.get(f"/conversations/{response.json()['conversation_id']}").json()

    model_user_message = next(
        item["content"] for item in reversed(llm.calls[0]["messages"]) if item["role"] == "user"
    )
    assert "<ui_context>" in model_user_message
    assert "任务 ID：task-1" in model_user_message
    assert "章节 ID：section-1" in model_user_message
    assert conversation["messages"][0]["content"] == "继续修改当前章节"


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
    assert any(
        item["function"]["name"].startswith("builtin_memory_")
        for item in llm.calls[1]["tools"]
    )
    assert all(
        item["function"]["name"].startswith(("builtin_memory_", "aina_"))
        for item in llm.calls[1]["tools"]
    )
    assert updated["active_aina_ids"] == ["unibot-documents", "unibot-memory"]
    assert updated["primary_aina_id"] == "unibot-documents"
    assert updated["last_aina_id"] == "unibot-memory"
    resolution = next(event for event in trace["events"] if event["kind"] == "routing.scope.resolved")
    assert resolution["target_id"] == "unibot-memory"
    assert resolution["details"]["source"] == "preferred_aina"
    assert "router_model_called" not in resolution["details"]


def test_short_follow_up_routes_with_all_candidates_and_last_aina_context(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            assistant("Started."),
            call_first_tool(
                prefix="aina_unibot-documents_",
                arguments="{}",
            ),
            assistant("Continued."),
        ]
    )
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
    assert len(llm.calls) == 3
    assert len(llm.calls[1]["tools"]) == 7
    assert any(item["function"]["name"].startswith("builtin_list_app_") for item in llm.calls[1]["tools"])
    assert any(
        item["function"]["name"].startswith("builtin_document_")
        for item in llm.calls[2]["tools"]
    )
    assert all(
        item["function"]["name"].startswith(("builtin_", "aina_"))
        for item in llm.calls[2]["tools"]
    )
    resolution = next(
        event
        for event in trace["events"]
        if event["kind"] == "routing.scope.resolved" and event["details"]["source"] == "model_selection"
    )
    assert resolution["target_id"] == "unibot-documents"
    assert resolution["details"]["last_aina_id"] == "unibot-documents"
    assert "router_model_called" not in resolution["details"]


def test_single_active_primary_aina_is_context_but_does_not_limit_router(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="aina_unibot-documents_",
                arguments="{}",
            ),
            assistant("Handled by the primary document AINA."),
        ]
    )
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
    assert len(llm.calls) == 2
    assert len(llm.calls[0]["tools"]) == 7
    assert any(item["function"]["name"].startswith("builtin_open_aina_") for item in llm.calls[0]["tools"])
    resolution = next(
        event
        for event in trace["events"]
        if event["kind"] == "routing.scope.resolved" and event["details"]["source"] == "model_selection"
    )
    assert resolution["target_id"] == "unibot-documents"
    assert resolution["details"]["primary_aina_id"] == "unibot-documents"
    assert "router_model_called" not in resolution["details"]


def test_ambiguous_turn_routes_across_active_ainas_with_model(tmp_path: Path) -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                prefix="aina_unibot-memory_",
                arguments="{}",
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
    assert len(llm.calls[0]["tools"]) == 7
    assert any(item["function"]["name"].startswith("builtin_list_app_") for item in llm.calls[0]["tools"])
    resolution = next(
        event
        for event in trace["events"]
        if event["kind"] == "routing.scope.resolved" and event["details"]["source"] == "model_selection"
    )
    assert resolution["target_id"] == "unibot-memory"
    assert "router_model_called" not in resolution["details"]


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
