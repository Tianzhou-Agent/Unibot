"""Per-user records in SQL, shared by several repository instances (replicas).

Workspaces, memories, model providers, feedback, document edit tasks and installations. AINA projects are covered by
``tests/test_aina_projects.py``. Runs on SQLite; concurrent writes are covered by ``test_user_record_stores_mysql.py``.
"""

from __future__ import annotations

import pytest

from tianzhou_agent_platform.aina.document.task_models import DocumentDraftSection, DocumentEditTask
from tianzhou_agent_platform.aina.memory.models import MemoryCreate, MemoryUpdate
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.feedback import FeedbackRecord
from tianzhou_agent_platform.core.workspace import WorkspaceCreate, WorkspaceUpdate
from tianzhou_agent_platform.model_providers.models import (
    ModelDefinitionInput,
    ModelProviderCreate,
    ModelProviderUpdate,
)
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.repository import PersistentRepository
from tests.support.sqlite_store import sqlite_session_factory


class _SqlOnly:
    def __init__(self) -> None:
        self.session_factory = sqlite_session_factory()


def _replicas() -> tuple[PersistentRepository, PersistentRepository]:
    stores = StorageStores(mysql=_SqlOnly(), redis=None, nas=None)  # type: ignore[arg-type]
    return PersistentRepository(stores), PersistentRepository(stores)


async def _denied(call) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(PlatformError) as error:
        await call
    return error.value.code


async def test_workspaces_are_shared_and_listed_newest_first() -> None:
    first, second = _replicas()
    older = await first.create_workspace(WorkspaceCreate(user_id="u1", tenant_id="t1", name="Old"))
    newer = await first.create_workspace(WorkspaceCreate(user_id="u1", tenant_id="t1", name="New"))
    await first.create_workspace(WorkspaceCreate(user_id="u2", tenant_id="t1", name="Theirs"))

    renamed = await second.update_workspace(older.id, WorkspaceUpdate(name="Renamed"))

    assert await first.get_workspace(older.id) == renamed
    assert [item.id for item in await first.list_workspaces(user_id="u1", tenant_id="t1")] == [older.id, newer.id]
    assert await _denied(second.require_workspace_actor(older.id, user_id="u2", tenant_id="t1")) == "PERMISSION_DENIED"
    assert await _denied(first.get_workspace("workspace_missing")) == "RESOURCE_NOT_FOUND"


async def test_memories_deduplicate_search_and_enforce_ownership() -> None:
    first, second = _replicas()
    fact = await first.create_memory(MemoryCreate(content="Prefers Python", user_id="u1", tenant_id="t1"))
    goal = await first.create_memory(
        MemoryCreate(content="Ship the 50% rollout", category="goal", user_id="u1", tenant_id="t1")
    )

    assert await second.create_memory(MemoryCreate(content="prefers python", user_id="u1", tenant_id="t1")) == fact
    assert [item.id for item in await second.list_memories(user_id="u1", tenant_id="t1", query="PYTHON")] == [fact.id]
    assert [item.id for item in await second.list_memories(user_id="u1", tenant_id="t1", query="%")] == [goal.id]
    assert [item.id for item in await second.search_memories("python", user_id="u1", tenant_id="t1")] == [fact.id]
    assert (await second.memory_stats(user_id="u1", tenant_id="t1")).goal == 1

    updated = await second.update_memory(fact.id, MemoryUpdate(content="Prefers Rust", user_id="u1", tenant_id="t1"))
    assert await first.get_memory(fact.id, user_id="u1", tenant_id="t1") == updated
    assert await _denied(first.get_memory(fact.id, user_id="u2", tenant_id="t1")) == "PERMISSION_DENIED"
    assert (
        await _denied(first.update_memory(fact.id, MemoryUpdate(content="x", user_id="u2", tenant_id="t1")))
        == "PERMISSION_DENIED"
    )
    await second.remove_memory(fact.id, user_id="u1", tenant_id="t1")
    assert await _denied(first.get_memory(fact.id, user_id="u1", tenant_id="t1")) == "RESOURCE_NOT_FOUND"


def _provider(name: str, user_id: str = "u1") -> ModelProviderCreate:
    return ModelProviderCreate(
        user_id=user_id,
        tenant_id="t1",
        provider_type="custom",
        name=name,
        base_url="https://models.example/v1",
        api_key="secret",
        models=[ModelDefinitionInput(name="Fast", model="fast-1"), ModelDefinitionInput(name="Slow", model="slow-1")],
    )


async def test_one_default_model_across_an_actors_providers() -> None:
    first, second = _replicas()
    a = await first.create_model_provider(_provider("A"))
    b = await first.create_model_provider(_provider("B"))
    others = await first.create_model_provider(_provider("Theirs", user_id="u2"))
    await second.set_default_model(others.id, others.models[0].id, user_id="u2", tenant_id="t1")

    await first.set_default_model(a.id, a.models[0].id, user_id="u1", tenant_id="t1")
    await second.set_default_model(b.id, b.models[1].id, user_id="u1", tenant_id="t1")

    runtime = await first.get_default_model_runtime(user_id="u1", tenant_id="t1")
    assert runtime is not None and (runtime.provider_id, runtime.model) == (b.id, "slow-1")
    defaults = [
        (provider.id, model.id)
        for provider in await first.list_model_providers(user_id="u1", tenant_id="t1")
        for model in provider.models
        if model.is_default
    ]
    assert defaults == [(b.id, b.models[1].id)]
    assert (await first.get_default_model_runtime(user_id="u2", tenant_id="t1")).provider_id == others.id  # type: ignore[union-attr]
    assert (
        await _denied(second.set_default_model(a.id, a.models[0].id, user_id="u2", tenant_id="t1"))
        == "PERMISSION_DENIED"
    )


async def test_provider_edits_keep_the_key_unless_replaced_and_removal_is_shared() -> None:
    first, second = _replicas()
    provider = await first.create_model_provider(_provider("A"))
    update = ModelProviderUpdate.model_validate(
        {
            **_provider("Renamed").model_dump(),
            "api_key": None,
            "models": [provider.models[0].model_dump(exclude={"is_default"})],
        }
    )

    updated = await second.update_model_provider(provider.id, update)

    assert (updated.name, updated.api_key, len(updated.models)) == ("Renamed", "secret", 1)
    assert await first.get_model_provider(provider.id, user_id="u1", tenant_id="t1") == updated
    await first.remove_model_provider(provider.id, user_id="u1", tenant_id="t1")
    assert await second.list_model_providers(user_id="u1", tenant_id="t1") == []


def _feedback(feedback_id: str, rating: str = "down", **fields: object) -> FeedbackRecord:
    return FeedbackRecord.model_validate(
        {
            "id": feedback_id,
            "user_id": "u1",
            "tenant_id": "t1",
            "user_name": "Ann",
            "user_email": "ann@example.com",
            "conversation_id": "conv_1",
            "message_id": "msg_1",
            "rating": rating,
            **fields,
        }
    )


async def test_one_feedback_per_message_through_resubmission_and_cancellation() -> None:
    first, second = _replicas()
    created = await first.upsert_feedback(_feedback("fb_1", rating="up"))
    resubmitted = await second.upsert_feedback(_feedback("fb_2", comment="wrong answer"))

    assert resubmitted.id == created.id
    assert (resubmitted.rating, resubmitted.comment, len(resubmitted.history)) == ("down", "wrong answer", 1)

    await first.cancel_feedback("msg_1", user_id="u1", tenant_id="t1", actor_name="Ann")
    assert await second.get_feedback_for_message("msg_1", user_id="u1", tenant_id="t1") is None
    assert await second.list_feedbacks() == []
    assert await _denied(second.get_feedback(created.id)) == "RESOURCE_NOT_FOUND"

    restored = await second.upsert_feedback(_feedback("fb_3"))
    assert restored.id == created.id and restored.active
    case = await first.update_feedback_case(
        created.id, status="resolved", assignee="Ben", conclusion="Fixed", actor_id="admin", actor_name="Admin"
    )
    assert [item.id for item in await second.list_feedbacks(rating="down", user_query="ANN@")] == [case.id]
    assert (await second.get_feedback(created.id)).case_status == "resolved"


def _document_task() -> DocumentEditTask:
    return DocumentEditTask(
        document_name="plan.md",
        title="Tighten",
        description="Shorten the intro",
        base_revision="rev1",
        user_id="u1",
        tenant_id="t1",
        sections=[DocumentDraftSection(heading="Intro", occurrence=0, level=1, base_content="a", draft_content="b")],
    )


async def test_document_task_saves_are_rejected_after_another_replica_saved() -> None:
    first, second = _replicas()
    task = await first.create_document_edit_task(_document_task())

    saved = await second.put_document_edit_task(task.model_copy(update={"status": "reviewing"}), expected_version=1)
    assert saved.version == 2
    assert await _denied(first.put_document_edit_task(task, expected_version=1)) == "CONFLICT"
    assert await first.get_document_edit_task(task.id, user_id="u1", tenant_id="t1") == saved
    assert [item.id for item in await first.list_document_edit_tasks(document_name="plan.md")] == [task.id]
    assert await _denied(first.put_document_edit_task(_document_task(), expected_version=1)) == "RESOURCE_NOT_FOUND"
    assert await _denied(first.get_document_edit_task(task.id, user_id="u2", tenant_id="t1")) == "PERMISSION_DENIED"


async def test_installations_are_shared_and_removed_with_their_aina() -> None:
    first, second = _replicas()
    for user_id, aina_id in (("u1", "aina.a"), ("u2", "aina.a"), ("u1", "aina.b")):
        await first.put_installation(
            AinaInstallation(user_id=user_id, tenant_id="t1", aina_id=aina_id, installed_version="1.0")
        )
    disabled = await second.put_installation(
        AinaInstallation(user_id="u1", tenant_id="t1", aina_id="aina.b", installed_version="1.0", status="disabled")
    )

    assert await first.get_installation(tenant_id="t1", user_id="u1", aina_id="aina.b") == disabled
    assert len(await first.list_installations(tenant_id="t1", user_id="u1")) == 2

    # The SQL half of remove_aina; the AINA registry itself is still per process until it moves to SQL.
    await second._aina_project_store.remove_installations_of("aina.a")  # noqa: SLF001
    assert [item.aina_id for item in await first.list_installations()] == ["aina.b"]
    await first.remove_installation(tenant_id="t1", user_id="u1", aina_id="aina.b")
    assert (
        await _denied(second.get_installation(tenant_id="t1", user_id="u1", aina_id="aina.b")) == "RESOURCE_NOT_FOUND"
    )
