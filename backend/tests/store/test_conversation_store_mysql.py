"""Concurrent conversation writes from two replicas (separate connection pools) on one live MySQL database.

Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import select

from tianzhou_agent_platform.conversations.models import ConversationCreate, Message
from tianzhou_agent_platform.store.lifecycle import create_storage_stores
from tianzhou_agent_platform.store.repository import PersistentRepository, repository_tables
from tianzhou_agent_platform.store.repository_schema import messages_table
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


async def test_concurrent_appends_keep_every_message_with_contiguous_sequence(replicas) -> None:  # type: ignore[no-untyped-def]
    first, second = replicas
    conversation = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))

    await asyncio.gather(
        *(
            replica.append_provider_messages(
                conversation.id, [{"role": "user", "content": f"{name}-{index}"}], trace_id="tr"
            )
            for index in range(20)
            for name, replica in (("first", first), ("second", second))
        )
    )

    stored = (await second.get_conversation(conversation.id)).messages
    assert sorted(message.content for message in stored) == sorted(
        f"{name}-{index}" for index in range(20) for name in ("first", "second")
    )
    async with first.stores.mysql.session_factory() as session:
        sequence = list(
            (
                await session.execute(
                    select(messages_table.c.sequence_no)
                    .where(messages_table.c.conversation_id == conversation.id)
                    .order_by(messages_table.c.sequence_no)
                )
            ).scalars()
        )
    assert sequence == list(range(40))


async def test_the_same_retried_batch_is_stored_once(replicas) -> None:  # type: ignore[no-untyped-def]
    first, second = replicas
    conversation = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))
    batch = [Message(id=f"m{index}", role="user", content=str(index)) for index in range(5)]

    await asyncio.gather(*(replica.append_messages(conversation.id, batch) for replica in (first, second) * 3))

    stored = (await first.get_conversation(conversation.id)).messages
    assert [message.id for message in stored] == [message.id for message in batch]


async def test_concurrent_updates_do_not_lose_each_other(replicas) -> None:  # type: ignore[no-untyped-def]
    first, second = replicas
    conversation = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))
    aina_ids = [f"aina.{index}" for index in range(10)]

    await asyncio.gather(
        *(
            (first if index % 2 else second).bind_conversation_aina(conversation.id, aina_id)
            for index, aina_id in enumerate(aina_ids)
        )
    )

    assert sorted((await second.get_conversation(conversation.id)).active_aina_ids) == aina_ids
