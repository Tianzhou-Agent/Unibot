"""Scheduled AINA tasks and their execution history, read and written in SQL."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import insert, select

from tianzhou_agent_platform.aina.scheduler import ScheduledAinaExecution, ScheduledAinaTask, ScheduledAinaTaskUpdate
from tianzhou_agent_platform.core.errors import not_found
from tianzhou_agent_platform.store.memory_repository import updated_scheduled_aina_task
from tianzhou_agent_platform.store.repository_schema import (
    scheduled_aina_executions_table,
    scheduled_aina_tasks_table,
    table_values,
)
from tianzhou_agent_platform.store.sql import SqlStore

tasks = scheduled_aina_tasks_table
executions = scheduled_aina_executions_table


class ScheduleStore(SqlStore):
    async def create_task(self, task: ScheduledAinaTask) -> None:
        async with self._session() as session, session.begin():
            await session.execute(insert(tasks).values(table_values(tasks, task)))

    async def put_task(self, task: ScheduledAinaTask) -> None:
        await self._put(tasks, table_values(tasks, task))

    async def get_task(self, task_id: str) -> ScheduledAinaTask | None:
        return await self._one(ScheduledAinaTask, select(tasks).where(tasks.c.id == task_id))

    async def list_tasks(self, *, user_id: str | None, tenant_id: str | None) -> list[ScheduledAinaTask]:
        statement = select(tasks)
        if user_id is not None:
            statement = statement.where(tasks.c.user_id == user_id)
        if tenant_id is not None:
            statement = statement.where(tasks.c.tenant_id == tenant_id)
        return await self._all(ScheduledAinaTask, statement.order_by(tasks.c.created_at, tasks.c.id))

    async def list_due_tasks(self, now: datetime) -> list[ScheduledAinaTask]:
        statement = (
            select(tasks)
            .where(tasks.c.enabled.is_(True), tasks.c.next_run_at <= now)
            .order_by(tasks.c.next_run_at, tasks.c.id)
        )
        return await self._all(ScheduledAinaTask, statement)

    async def update_task(self, task_id: str, data: ScheduledAinaTaskUpdate) -> ScheduledAinaTask:
        updated = await self.change_task(task_id, lambda task: updated_scheduled_aina_task(task, data))
        if updated is None:
            raise not_found("Scheduled AINA task", task_id)
        return updated

    async def change_task(
        self, task_id: str, change: Callable[[ScheduledAinaTask], ScheduledAinaTask | None]
    ) -> ScheduledAinaTask | None:
        return await self._update_locked(tasks, ScheduledAinaTask, task_id, change)

    async def remove_task(self, task_id: str) -> bool:
        return await self._delete(tasks, tasks.c.id == task_id, (executions, executions.c.task_id == task_id)) > 0

    async def put_execution(self, execution: ScheduledAinaExecution) -> None:
        await self._put(executions, table_values(executions, execution))

    async def list_executions(self, task_id: str, *, limit: int) -> list[ScheduledAinaExecution]:
        statement = (
            select(executions)
            .where(executions.c.task_id == task_id)
            .order_by(executions.c.started_at.desc(), executions.c.id.desc())
            .limit(limit)
        )
        return await self._all(ScheduledAinaExecution, statement)
