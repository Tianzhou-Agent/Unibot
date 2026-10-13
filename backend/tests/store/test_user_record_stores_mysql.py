"""Concurrent per-user record writes from two replicas (separate connection pools) on one live MySQL database.

Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from tianzhou_agent_platform.aina.document.task_models import DocumentDraftSection, DocumentEditTask
from tianzhou_agent_platform.aina.memory.models import MemoryCreate, MemoryUpdate
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.feedback import FeedbackRecord
from tianzhou_agent_platform.core.workspace import WorkspaceCreate, WorkspaceUpdate
from tianzhou_agent_platform.model_providers.models import ModelDefinitionInput, ModelProviderCreate
from tianzhou_agent_platform.store.lifecycle import create_storage_stores
from tianzhou_agent_platform.store.repository import PersistentRepository, repository_tables
from tianzhou_agent_platform.store.settings import StorageSettings

pytestmark = pytest.mark.skipif(
    os.getenv("TZ_STORAGE_E2E") != "1",
    reason="Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml to run live storage E2E tests",
)


@pytest.fixture
async def replicas() -> AsyncIterator[tuple[PersistentRepository, PersistentRepository]]:
    settings = StorageSettings(
        mysql_dsn=os.getenv("TZ_STORAGE_E2E_MYSQL_DSN", "mysql+aiomysql://unibot:unibot@127.0.0.1:13306/unibot"),
        redis_dsn=os.getenv("TZ_STORAGE_E2E_REDIS_URL", "redis://127.0.0.1:16379/0"),
    )
    stores = [create_storage_stores(settings, mysql_resource_tables=repository_tables) for _ in range(2)]
    try:
        yield PersistentRepository(stores[0]), PersistentRepository(stores[1])
    finally:
        for item in stores:
            await item.close()


def _actor() -> str:
    return f"user_{uuid4().hex}"


async def test_concurrent_first_feedbacks_for_one_answer_share_one_record(replicas) -> None:  # type: ignore[no-untyped-def]
    first, second = replicas
    user_id = _actor()

    def feedback(index: int) -> FeedbackRecord:
        return FeedbackRecord(
            id=f"fb_{uuid4().hex}",
            user_id=user_id,
            tenant_id="t1",
            user_name="Ann",
            conversation_id="conv_1",
            message_id="msg_1",
            rating="down",
            comment=str(index),
        )

    stored = await asyncio.gather(
        *(replica.upsert_feedback(feedback(index)) for index, replica in enumerate((first, second) * 5))
    )

    assert len({item.id for item in stored}) == 1
    assert len(await first.list_feedbacks(user_query="ann")) >= 1
    assert await second.get_feedback_for_message("msg_1", user_id=user_id, tenant_id="t1") is not None


async def test_only_one_save_wins_from_the_same_document_task_version(replicas) -> None:  # type: ignore[no-untyped-def]
    first, second = replicas
    task = await first.create_document_edit_task(
        DocumentEditTask(
            document_name="plan.md",
            title="Tighten",
            description="Shorten",
            base_revision="rev1",
            user_id=_actor(),
            tenant_id="t1",
            sections=[
                DocumentDraftSection(heading="Intro", occurrence=0, level=1, base_content="a", draft_content="b")
            ],
        )
    )

    results = await asyncio.gather(
        *(
            replica.put_document_edit_task(task.model_copy(update={"title": f"edit {index}"}), expected_version=1)
            for index, replica in enumerate((first, second) * 3)
        ),
        return_exceptions=True,
    )

    saved = [result for result in results if isinstance(result, DocumentEditTask)]
    assert len(saved) == 1
    assert {result.code for result in results if isinstance(result, PlatformError)} == {"CONFLICT"}
    assert (await second.get_document_edit_task(task.id, user_id=task.user_id, tenant_id="t1")) == saved[0]


async def test_concurrent_default_model_choices_leave_exactly_one_default(replicas) -> None:  # type: ignore[no-untyped-def]
    first, second = replicas
    # Several rounds: a missing lock only shows when two changes interleave, which one round may not produce.
    for _ in range(10):
        user_id = _actor()
        providers = [
            await first.create_model_provider(
                ModelProviderCreate(
                    user_id=user_id,
                    tenant_id="t1",
                    provider_type="custom",
                    name=name,
                    base_url="https://models.example/v1",
                    models=[ModelDefinitionInput(name="m", model=f"{name}-model")],
                )
            )
            for name in ("a", "b", "c", "d")
        ]

        await asyncio.gather(
            *(
                (first if index % 2 else second).set_default_model(
                    provider.id, provider.models[0].id, user_id=user_id, tenant_id="t1"
                )
                for index, provider in enumerate(providers * 3)
            )
        )

        defaults = [
            model
            for provider in await first.list_model_providers(user_id=user_id, tenant_id="t1")
            for model in provider.models
            if model.is_default
        ]
        assert len(defaults) == 1


async def test_concurrent_edits_of_one_record_keep_every_field(replicas) -> None:  # type: ignore[no-untyped-def]
    first, second = replicas
    for _ in range(10):
        user_id = _actor()
        workspace = await first.create_workspace(WorkspaceCreate(user_id=user_id, tenant_id="t1", name="Old"))
        memory = await first.create_memory(MemoryCreate(content="Old fact", user_id=user_id, tenant_id="t1"))

        await asyncio.gather(
            first.update_workspace(workspace.id, WorkspaceUpdate(name="New")),
            second.update_workspace(workspace.id, WorkspaceUpdate(description="Shared notes")),
            first.update_memory(memory.id, MemoryUpdate(content="New fact", user_id=user_id, tenant_id="t1")),
            second.update_memory(memory.id, MemoryUpdate(category="goal", user_id=user_id, tenant_id="t1")),
        )

        stored_workspace = await second.get_workspace(workspace.id)
        stored_memory = await second.get_memory(memory.id, user_id=user_id, tenant_id="t1")
        assert (stored_workspace.name, stored_workspace.description) == ("New", "Shared notes")
        assert (stored_memory.content, stored_memory.category) == ("New fact", "goal")
