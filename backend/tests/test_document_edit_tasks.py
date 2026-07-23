import json
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.aina.document.task_models import (
    DocumentEditTaskCreate,
    DocumentSectionSelection,
)
from tianzhou_agent_platform.aina.document.task_service import (
    DocumentEditTaskService,
    DocumentEditWorker,
)
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.nas.filesystem import NasStore
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool


def _app(tmp_path: Path, llm: ScriptedLLM) -> FastAPI:
    return create_app(
        settings=AgentSettings(
            _env_file=None,
            llm_base_url="https://model.invalid/v1",
            llm_api_key="test-key",
            llm_model="test-model",
        ),
        llm=llm,
        document_service=DocumentService(NasStore(tmp_path)),
    )


def _draft(content: str):  # type: ignore[no-untyped-def]
    return call_first_tool(
        prefix="submit_document_section_draft",
        arguments=json.dumps({"section_content": content}, ensure_ascii=False),
    )


def _wait_for_review(client: TestClient, task_id: str) -> dict:  # type: ignore[type-arg]
    for _ in range(100):
        task = client.get(f"/document-edit-tasks/{task_id}").json()
        if task["status"] in {"reviewing", "failed"}:
            return task
        time.sleep(0.05)
    raise AssertionError("Document edit task did not finish")


def _wait_for_section(client: TestClient, task_id: str, section_id: str, revision: int) -> dict:  # type: ignore[type-arg]
    for _ in range(100):
        task = client.get(f"/document-edit-tasks/{task_id}").json()
        section = next(item for item in task["sections"] if item["id"] == section_id)
        if section["ai_status"] == "ready" and section["draft_revision"] == revision:
            return task
        time.sleep(0.05)
    raise AssertionError("Document draft section did not finish")


def test_nested_document_path_supports_edit_tasks(tmp_path: Path) -> None:
    llm = ScriptedLLM([_draft("## Intro\n\nNested draft.")])
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents/folders", json={"path": "Projects/Specs"})
        client.post(
            "/documents",
            json={"name": "Projects/Specs/guide", "content": "# Guide\n\n## Intro\n\nOld."},
        )
        created = client.post(
            "/documents/Projects/Specs/guide.md/edit-tasks",
            json={"description": "Rewrite intro", "sections": [{"heading": "Intro"}]},
        )
        task = _wait_for_review(client, created.json()["id"])
        listed = client.get("/documents/Projects/Specs/guide.md/edit-tasks").json()

    assert created.status_code == 202
    assert task["document_name"] == "Projects/Specs/guide.md"
    assert task["sections"][0]["draft_content"] == "## Intro\n\nNested draft."
    assert listed["items"][0]["id"] == task["id"]


def test_edit_task_generates_reviewable_drafts_and_merges_once(tmp_path: Path) -> None:
    original = "# Guide\n\n## One\n\nOld one.\n\n## Two\n\nOld two.\n"
    llm = ScriptedLLM(
        [
            _draft("## One\n\nAI one."),
            _draft("## Two\n\nAI two."),
            _draft("## Two\n\nAI two revised."),
        ]
    )
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "guide", "content": original})
        created = client.post(
            "/documents/guide.md/edit-tasks",
            json={
                "description": "Improve the selected chapters for publication",
                "sections": [
                    {"heading": "One", "occurrence": 1},
                    {"heading": "Two", "occurrence": 1},
                ],
            },
        )
        task = _wait_for_review(client, created.json()["id"])

        assert created.status_code == 202
        assert task["title"] == "Improve the selected chapters…"
        assert client.get("/documents/guide.md").json()["content"] == original

        first, second = task["sections"]
        stale = client.patch(
            f"/document-edit-tasks/{task['id']}/sections/{first['id']}",
            json={
                "content": "## One\n\nStale edit.",
                "expected_draft_revision": first["draft_revision"] - 1,
            },
        )
        assert stale.status_code == 409
        assert (
            client.get(
                f"/document-edit-tasks/{task['id']}",
                params={"user_id": "someone-else"},
            ).status_code
            == 403
        )
        edited = client.patch(
            f"/document-edit-tasks/{task['id']}/sections/{first['id']}",
            json={
                "content": "## One\n\nUser-reviewed one.",
                "expected_draft_revision": first["draft_revision"],
            },
        )
        assert edited.status_code == 200
        second = edited.json()["sections"][1]
        requested = client.post(
            f"/document-edit-tasks/{task['id']}/sections/{second['id']}/ai-revise",
            json={
                "instruction": "Make this more concise",
                "expected_draft_revision": second["draft_revision"],
            },
        )
        assert requested.status_code == 202
        task = _wait_for_section(client, task["id"], second["id"], second["draft_revision"] + 1)

        merged = client.post(f"/document-edit-tasks/{task['id']}/merge", json={})
        delete_completed = client.delete(f"/document-edit-tasks/{task['id']}")
        document = client.get("/documents/guide.md").json()["content"]

    assert merged.status_code == 200
    assert merged.json()["status"] == "merged"
    assert merged.json()["completed_at"] is not None
    assert all(item["result_revision"] for item in merged.json()["sections"])
    assert delete_completed.status_code == 409
    assert "User-reviewed one." in document
    assert "AI two revised." in document
    assert "Old one." not in document
    assert "Old two." not in document


def test_edit_task_sections_can_be_merged_or_abandoned_independently(tmp_path: Path) -> None:
    original = "# Guide\n\n## One\n\nOld one.\n\n## Two\n\nOld two.\n\n## Three\n\nOld three.\n"
    llm = ScriptedLLM([
        _draft("## One\n\nAI one."),
        _draft("## Two\n\nAI two."),
        _draft("## Three\n\nAI three."),
    ])
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "guide", "content": original})
        created = client.post(
            "/documents/guide.md/edit-tasks",
            json={
                "description": "Rewrite both",
                "sections": [{"heading": "One"}, {"heading": "Two"}, {"heading": "Three"}],
            },
        ).json()
        task = _wait_for_review(client, created["id"])
        first, second, third = task["sections"]

        merged = client.post(
            f"/document-edit-tasks/{task['id']}/sections/{first['id']}/merge",
            json={},
        )
        merged_second = client.post(
            f"/document-edit-tasks/{task['id']}/sections/{second['id']}/merge",
            json={},
        )
        abandoned = client.post(
            f"/document-edit-tasks/{task['id']}/sections/{third['id']}/abandon",
            json={},
        )
        document = client.get("/documents/guide.md").json()["content"]

    assert merged.status_code == 200
    assert merged.json()["status"] == "reviewing"
    assert merged.json()["sections"][0]["review_status"] == "merged"
    assert merged_second.status_code == 200
    assert merged_second.json()["status"] == "reviewing"
    assert merged_second.json()["sections"][1]["review_status"] == "merged"
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == "completed"
    assert abandoned.json()["sections"][2]["review_status"] == "abandoned"
    assert "AI one." in document
    assert "Old one." not in document
    assert "AI two." in document
    assert "Old two." not in document
    assert "Old three." in document
    assert "AI three." not in document


def test_failed_task_can_retry_unfinished_sections_and_delete_abandoned_result(tmp_path: Path) -> None:
    llm = ScriptedLLM([
        assistant("The model did not call the draft tool."),
        _draft("## One\n\nRecovered draft."),
    ])
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "guide", "content": "# Guide\n\n## One\n\nOld.\n"})
        created = client.post(
            "/documents/guide.md/edit-tasks",
            json={"description": "Rewrite the section", "sections": [{"heading": "One"}]},
        ).json()
        failed = _wait_for_review(client, created["id"])
        retried = client.post(f"/document-edit-tasks/{created['id']}/retry", json={})
        reviewed = _wait_for_review(client, created["id"])
        abandoned = client.post(f"/document-edit-tasks/{created['id']}/abandon", json={})
        deleted = client.delete(f"/document-edit-tasks/{created['id']}")
        listed = client.get("/documents/guide.md/edit-tasks").json()

    assert failed["status"] == "failed"
    assert failed["sections"][0]["ai_status"] == "failed"
    assert retried.status_code == 202
    assert retried.json()["attempt_count"] == 2
    assert reviewed["status"] == "reviewing"
    assert reviewed["sections"][0]["draft_content"] == "## One\n\nRecovered draft."
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == "abandoned"
    assert abandoned.json()["completed_at"] is not None
    assert deleted.status_code == 204
    assert listed["items"] == []


def test_failed_task_can_be_soft_deleted_before_any_section_is_merged(tmp_path: Path) -> None:
    llm = ScriptedLLM([assistant("The model did not call the draft tool.")])
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "guide", "content": "# Guide\n\n## One\n\nOld.\n"})
        created = client.post(
            "/documents/guide.md/edit-tasks",
            json={"description": "Rewrite the section", "sections": [{"heading": "One"}]},
        ).json()
        failed = _wait_for_review(client, created["id"])
        deleted = client.delete(f"/document-edit-tasks/{created['id']}")
        listed = client.get("/documents/guide.md/edit-tasks").json()
        stored = client.get(f"/document-edit-tasks/{created['id']}").json()

    assert failed["status"] == "failed"
    assert deleted.status_code == 204
    assert listed["items"] == []
    assert stored["status"] == "deleted"
    assert stored["deleted_at"] is not None


def test_edit_task_rejects_overlapping_sections(tmp_path: Path) -> None:
    llm = ScriptedLLM([])
    with TestClient(_app(tmp_path, llm)) as client:
        client.post(
            "/documents",
            json={"name": "guide", "content": "# Guide\n\n## One\n\n### Child\n\nText.\n"},
        )
        response = client.post(
            "/documents/guide.md/edit-tasks",
            json={
                "description": "Rewrite both",
                "sections": [
                    {"heading": "One", "occurrence": 1},
                    {"heading": "Child", "occurrence": 1},
                ],
            },
        )

    assert response.status_code == 409


def test_edit_task_merge_detects_changed_source_revision(tmp_path: Path) -> None:
    llm = ScriptedLLM([_draft("## One\n\nFirst draft."), _draft("## One\n\nSecond draft.")])
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "guide", "content": "# Guide\n\n## One\n\nOld.\n"})
        first = client.post(
            "/documents/guide.md/edit-tasks",
            json={"description": "First change", "sections": [{"heading": "One"}]},
        ).json()
        second = client.post(
            "/documents/guide.md/edit-tasks",
            json={"description": "Second change", "sections": [{"heading": "One"}]},
        ).json()
        _wait_for_review(client, first["id"])
        _wait_for_review(client, second["id"])
        assert client.post(f"/document-edit-tasks/{first['id']}/merge", json={}).status_code == 200
        conflicted = client.post(f"/document-edit-tasks/{second['id']}/merge", json={})
        second_after = client.get(f"/document-edit-tasks/{second['id']}").json()

    assert conflicted.status_code == 409
    assert second_after["status"] == "conflict"


def test_existing_document_only_exposes_section_edit_and_reviewed_task_modes(tmp_path: Path) -> None:
    llm = ScriptedLLM([])
    with TestClient(_app(tmp_path, llm)) as client:
        client.post("/documents", json={"name": "guide", "content": "# Guide\n\n## Intro\n\nOld."})
        section = client.get("/documents/guide.md/sections", params={"heading": "Intro"}).json()
        root = client.get("/documents/guide.md/sections", params={"heading": "Guide"}).json()
        full_update = client.put("/documents/guide.md", json={"content": "# Changed"})
        root_update = client.put(
            "/documents/guide.md/sections",
            json={
                "heading": "Guide",
                "section_content": "# Replaced\n\n## Intro\n\nChanged.",
                "expected_revision": root["revision"],
            },
        )
        root_task = client.post(
            "/documents/guide.md/edit-tasks",
            json={"description": "Replace everything", "sections": [{"heading": "Guide"}]},
        )
        direct = client.put(
            "/documents/guide.md/sections",
            json={
                "heading": "Intro",
                "section_content": "## Intro\n\nChanged.",
                "expected_revision": section["revision"],
            },
        )
        ainas = client.get("/ainas").json()

    document_aina = next(item for item in ainas if item["manifest"]["aina"]["id"] == "unibot-documents")
    tool_ids = {item["id"] for item in document_aina["manifest"]["capabilities"]["tools"]}
    assert direct.status_code == 200
    assert full_update.status_code == 405
    assert root_update.status_code == 422
    assert root_task.status_code == 422
    assert "document.update" not in tool_ids
    assert {"document.update_section", "document.append"} <= tool_ids
    assert {
        "document.edit_task.create",
        "document.edit_task.list",
        "document.edit_task.read",
        "document.edit_task.update_draft",
        "document.edit_task.ai_revise",
        "document.edit_task.retry",
        "document.edit_task.merge_section",
        "document.edit_task.abandon_section",
    } <= tool_ids


@pytest.mark.asyncio
async def test_worker_recovers_a_task_interrupted_after_drafts_finished(tmp_path: Path) -> None:
    documents = DocumentService(NasStore(tmp_path))
    repository = InMemoryRepository()
    service = DocumentEditTaskService(documents, repository, ScriptedLLM([]))
    await documents.create_document(
        "guide",
        "# Guide\n\n## One\n\nOld.\n",
        user_id="anonymous",
        tenant_id="default",
    )
    task = await service.create_task(
        "guide.md",
        DocumentEditTaskCreate(
            description="Improve the section",
            sections=[DocumentSectionSelection(heading="One")],
        ),
    )
    ready_section = task.sections[0].model_copy(update={"ai_status": "ready"})
    interrupted = await repository.put_document_edit_task(
        task.model_copy(update={"status": "running", "sections": [ready_section]}),
        expected_version=task.version,
    )

    await DocumentEditWorker(service).tick()
    recovered = await service.get_task(
        interrupted.id,
        user_id="anonymous",
        tenant_id="default",
    )

    assert recovered.status == "reviewing"
