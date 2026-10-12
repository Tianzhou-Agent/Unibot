"""Concurrent account creation from two replicas (separate connection pools) on one live MySQL database.

Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest

from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.core.errors import PlatformError
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


async def test_concurrent_registrations_of_one_email_create_one_user(replicas) -> None:  # type: ignore[no-untyped-def]
    first, second = replicas
    email = f"race-{uuid4().hex[:8]}@example.com"

    results = await asyncio.gather(
        *(
            replica.create_user(UserRecord(id=f"user_{uuid4().hex}", email=variant, name="Racer"))
            for replica, variant in ((first, email), (second, email.upper())) * 5
        ),
        return_exceptions=True,
    )

    created = [result for result in results if isinstance(result, UserRecord)]
    rejected = [result for result in results if isinstance(result, PlatformError)]
    assert len(created) == 1 and len(rejected) == 9
    assert {error.user_message for error in rejected} == {"该邮箱已注册。"}
    assert (await second.find_user_by_email(email)) == created[0]


async def test_concurrent_first_github_sign_ins_share_one_user(replicas) -> None:  # type: ignore[no-untyped-def]
    first, second = replicas
    github_id = uuid4().hex[:12]

    users = await asyncio.gather(
        *(
            replica.upsert_github_user(
                github_id=github_id,
                github_login="octo",
                email=f"{github_id}@example.com",
                name="Octo",
                avatar_url=None,
            )
            for replica in (first, second) * 5
        )
    )

    assert len({user.id for user in users}) == 1
