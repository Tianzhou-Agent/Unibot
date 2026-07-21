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
from tests.support.fake_llm import ScriptedLLM, call_first_tool


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
        document = client.get("/documents/guide.md").json()["content"]

    assert merged.status_code == 200
    assert merged.json()["status"] == "merged"
    assert "User-reviewed one." in document
    assert "AI two revised." in document
    assert "Old one." not in document
    assert "Old two." not in document


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
        "document.edit_task.merge",
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
