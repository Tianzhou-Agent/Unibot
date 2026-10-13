"""Replicas cold-starting together on an empty registry in one live MySQL database.

Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete

from tianzhou_agent_platform.aina.builtin import ensure_builtin_ainas
from tianzhou_agent_platform.store.lifecycle import create_storage_stores
from tianzhou_agent_platform.store.repository import PersistentRepository, repository_tables
from tianzhou_agent_platform.store.repository_schema import ainas_table
from tianzhou_agent_platform.store.settings import StorageSettings

pytestmark = pytest.mark.skipif(
    os.getenv("TZ_STORAGE_E2E") != "1",
    reason="Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml to run live storage E2E tests",
)


@pytest.fixture
async def replicas() -> AsyncIterator[list[PersistentRepository]]:
    settings = StorageSettings(
        mysql_dsn=os.getenv("TZ_STORAGE_E2E_MYSQL_DSN", "mysql+aiomysql://unibot:unibot@127.0.0.1:13306/unibot"),
        redis_dsn=os.getenv("TZ_STORAGE_E2E_REDIS_URL", "redis://127.0.0.1:16379/0"),
    )
    stores = [create_storage_stores(settings, mysql_resource_tables=repository_tables) for _ in range(4)]
    try:
        yield [PersistentRepository(item) for item in stores]
    finally:
        for item in stores:
            await item.close()


async def test_replicas_starting_together_on_an_empty_registry_all_start(replicas) -> None:  # type: ignore[no-untyped-def]
    # Several rounds: the race only shows when registrations interleave.
    for _ in range(5):
        async with replicas[0].stores.mysql.session_factory() as session, session.begin():
            await session.execute(delete(ainas_table))

        await asyncio.gather(*(ensure_builtin_ainas(replica, document_enabled=True) for replica in replicas))

        ainas = await replicas[1].list_ainas()
        assert ainas and len(ainas) == len({aina.manifest.aina.id for aina in ainas})
