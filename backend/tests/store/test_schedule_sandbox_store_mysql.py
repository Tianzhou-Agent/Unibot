"""Schedule and sandbox writes from two replicas (separate connection pools) on one live MySQL database.

Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from tianzhou_agent_platform.aina.scheduler import ScheduledAinaTaskCreate, ScheduledAinaTaskUpdate
from tianzhou_agent_platform.sandbox.models import SandboxRecord
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


def _sandbox() -> SandboxRecord:
    sandbox_id = f"sandbox_{uuid4().hex}"
    return SandboxRecord(
        id=sandbox_id,
        user_id=sandbox_id,
        tenant_id="t1",
        image="python:3.12",
        driver="local",
        runtime_name=sandbox_id,
        workspace="/workspace",
    )


async def test_saving_an_unchanged_record_again_succeeds(replicas) -> None:  # type: ignore[no-untyped-def]
    # MySQL reports zero affected rows for an update that changes nothing unless the client counts matched rows;
    # the insert-or-replace path must not mistake that for a missing row.
    first, second = replicas
    sandbox = _sandbox()

    await first.put_sandbox(sandbox)
    await second.put_sandbox(sandbox)

    assert await first.get_sandbox_for_actor(user_id=sandbox.user_id, tenant_id="t1") == sandbox


async def test_concurrent_saves_of_one_new_record_all_succeed(replicas) -> None:  # type: ignore[no-untyped-def]
    first, second = replicas
    sandbox = _sandbox()

    await asyncio.gather(*(replica.put_sandbox(sandbox) for replica in (first, second) * 5))

    assert await second.get_sandbox_for_actor(user_id=sandbox.user_id, tenant_id="t1") == sandbox


async def test_concurrent_schedule_edits_do_not_lose_each_other(replicas) -> None:  # type: ignore[no-untyped-def]
    first, second = replicas
    task = await first.create_scheduled_aina_task(
        ScheduledAinaTaskCreate(aina_id="aina.remote", user_id="u1", tenant_id="t1", name="digest")
    )

    await asyncio.gather(
        first.update_scheduled_aina_task(task.id, ScheduledAinaTaskUpdate(name="renamed")),
        second.update_scheduled_aina_task(task.id, ScheduledAinaTaskUpdate(enabled=False)),
        first.update_scheduled_aina_task(task.id, ScheduledAinaTaskUpdate(prompt="new prompt")),
    )

    stored = await second.get_scheduled_aina_task(task.id)
    assert (stored.name, stored.enabled, stored.input) == ("renamed", False, {"message": "new prompt"})
