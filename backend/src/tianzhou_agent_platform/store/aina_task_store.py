"""Long-running AINA tasks (design §7.3), read and written in SQL."""

from __future__ import annotations

from typing import Callable

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError

from tianzhou_agent_platform.aina.package.tasks import AinaTask
from tianzhou_agent_platform.core.errors import conflict
from tianzhou_agent_platform.store.repository_schema import aina_tasks_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore

tasks = aina_tasks_table


class AinaTaskStore(SqlStore):
    async def create(self, task: AinaTask) -> None:
        try:
            async with self._session() as session, session.begin():
                await session.execute(insert(tasks).values(table_values(tasks, task)))
        except IntegrityError as exc:
            raise conflict(f"Task {task.external_id!r} already exists") from exc

    async def get(self, task_id: str) -> AinaTask | None:
        return await self._one(AinaTask, select(tasks).where(tasks.c.id == task_id))

    async def update(self, task_id: str, change: Callable[[AinaTask], AinaTask | None]) -> AinaTask | None:
        return await self._update_locked(tasks, AinaTask, task_id, change)

    async def list(self, conversation_id: str) -> list[AinaTask]:
        statement = select(tasks).where(tasks.c.conversation_id == conversation_id).order_by(tasks.c.created_at)
        return await self._all(AinaTask, statement)
