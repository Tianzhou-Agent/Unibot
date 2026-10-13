"""Document edit tasks, read and written in SQL.

Saves are optimistic: a task is replaced only while its stored version is the one the caller read.
"""

from __future__ import annotations

from sqlalchemy import insert, select, update

from tianzhou_agent_platform.aina.document.task_models import DocumentEditTask
from tianzhou_agent_platform.core.errors import not_found
from tianzhou_agent_platform.store.memory_repository import document_edit_task_changed, next_document_edit_task_version
from tianzhou_agent_platform.store.repository_schema import document_edit_tasks_table, table_values
from tianzhou_agent_platform.store.sql import SqlStore

tasks = document_edit_tasks_table


class DocumentTaskStore(SqlStore):
    async def create(self, task: DocumentEditTask) -> None:
        async with self._session() as session, session.begin():
            await session.execute(insert(tasks).values(table_values(tasks, task)))

    async def get(self, task_id: str) -> DocumentEditTask | None:
        return await self._one(DocumentEditTask, select(tasks).where(tasks.c.id == task_id))

    async def list_tasks(
        self,
        *,
        user_id: str | None,
        tenant_id: str | None,
        document_name: str | None,
        workspace_id: str | None,
    ) -> list[DocumentEditTask]:
        statement = select(tasks)
        for column, value in (
            (tasks.c.user_id, user_id),
            (tasks.c.tenant_id, tenant_id),
            (tasks.c.document_name, document_name),
            (tasks.c.workspace_id, workspace_id),
        ):
            if value is not None:
                statement = statement.where(column == value)
        return await self._all(DocumentEditTask, statement.order_by(tasks.c.created_at.desc(), tasks.c.id.desc()))

    async def put(self, task: DocumentEditTask, *, expected_version: int) -> DocumentEditTask:
        updated = next_document_edit_task_version(task, expected_version)
        async with self._session() as session, session.begin():
            result = await session.execute(
                update(tasks)
                .where(tasks.c.id == task.id, tasks.c.version == expected_version)
                .values(table_values(tasks, updated))
            )
        if not result.rowcount:  # type: ignore[attr-defined]
            if await self.get(task.id) is None:
                raise not_found("Document edit task", task.id)
            raise document_edit_task_changed()
        return updated
