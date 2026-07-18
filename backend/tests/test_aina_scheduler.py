from __future__ import annotations

import asyncio
from types import SimpleNamespace

from tianzhou_agent_platform.aina.scheduler import (
    AinaScheduler,
    ScheduledAinaTaskCreate,
    ScheduledAinaTaskUpdate,
)
from tianzhou_agent_platform.core.repository import InMemoryRepository


class SharedRedis:
    def __init__(self) -> None:
        self.keys: set[tuple[str, str]] = set()

    async def set_if_absent(self, namespace: str, key: str, value: object, *, ttl_seconds: int):
        marker = (namespace, key)
        written = marker not in self.keys
        self.keys.add(marker)
        return SimpleNamespace(written=written)


def test_distributed_scheduler_lease_has_a_single_winner() -> None:
    shared_redis = SharedRedis()
    first_repository = InMemoryRepository()
    second_repository = InMemoryRepository()
    first_repository.stores = SimpleNamespace(redis=shared_redis)  # type: ignore[attr-defined]
    second_repository.stores = SimpleNamespace(redis=shared_redis)  # type: ignore[attr-defined]
    first = AinaScheduler(first_repository, SimpleNamespace(), node_id="node-a")  # type: ignore[arg-type]
    second = AinaScheduler(second_repository, SimpleNamespace(), node_id="node-b")  # type: ignore[arg-type]

    async def claim() -> list[bool]:
        return list(await asyncio.gather(first._claim("task:run", 60), second._claim("task:run", 60)))

    assert asyncio.run(claim()).count(True) == 1


def test_scheduled_task_can_be_created_and_disabled() -> None:
    repository = InMemoryRepository()

    async def manage():
        created = await repository.create_scheduled_aina_task(
            ScheduledAinaTaskCreate(aina_id="report", name="Daily report", interval_seconds=60)
        )
        updated = await repository.update_scheduled_aina_task(
            created.id, ScheduledAinaTaskUpdate(enabled=False)
        )
        return updated, await repository.list_scheduled_aina_tasks()

    updated, tasks = asyncio.run(manage())
    assert updated.enabled is False
    assert tasks == [updated]
