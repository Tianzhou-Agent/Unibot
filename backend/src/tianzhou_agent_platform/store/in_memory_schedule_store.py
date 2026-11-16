"""Scheduled AINA tasks and their execution history, kept in process memory.

Has the methods of the SQL ``ScheduleStore``, so the repository works the same on either.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from tianzhou_agent_platform.aina.scheduler import (
    ScheduledAinaExecution,
    ScheduledAinaTask,
    ScheduledAinaTaskUpdate,
    updated_scheduled_aina_task,
)
from tianzhou_agent_platform.core.errors import not_found


class InMemoryScheduleStore:
    def __init__(self) -> None:
        self._tasks: dict[str, ScheduledAinaTask] = {}
        self._executions: dict[str, ScheduledAinaExecution] = {}

    async def create_task(self, task: ScheduledAinaTask) -> None:
        self._tasks[task.id] = task

    async def put_task(self, task: ScheduledAinaTask) -> None:
        self._tasks[task.id] = task

    async def get_task(self, task_id: str) -> ScheduledAinaTask | None:
        task = self._tasks.get(task_id)
        return None if task is None else task.model_copy(deep=True)

    async def list_tasks(self, *, user_id: str | None, tenant_id: str | None) -> list[ScheduledAinaTask]:
        return [
            item.model_copy(deep=True)
            for item in self._tasks.values()
            if (user_id is None or item.user_id == user_id) and (tenant_id is None or item.tenant_id == tenant_id)
        ]

    async def list_due_tasks(self, now: datetime) -> list[ScheduledAinaTask]:
        return [item.model_copy(deep=True) for item in self._tasks.values() if item.enabled and item.next_run_at <= now]

    async def update_task(self, task_id: str, data: ScheduledAinaTaskUpdate) -> ScheduledAinaTask:
        updated = await self.change_task(task_id, lambda task: updated_scheduled_aina_task(task, data))
        if updated is None:
            raise not_found("Scheduled AINA task", task_id)
        return updated

    async def change_task(
        self, task_id: str, change: Callable[[ScheduledAinaTask], ScheduledAinaTask | None]
    ) -> ScheduledAinaTask | None:
        """Apply ``change`` to the task; None when there is no such task.

        ``change`` returns the updated task, or None to leave the task as it is (the current task is returned).
        """
        task = self._tasks.get(task_id)
        if task is None:
            return None
        updated = change(task)
        if updated is None:
            return task.model_copy(deep=True)
        self._tasks[task_id] = updated
        return updated.model_copy(deep=True)

    async def remove_task(self, task_id: str) -> bool:
        if self._tasks.pop(task_id, None) is None:
            return False
        self._executions = {key: item for key, item in self._executions.items() if item.task_id != task_id}
        return True

    async def put_execution(self, execution: ScheduledAinaExecution) -> None:
        self._executions[execution.id] = execution

    async def list_executions(self, task_id: str, *, limit: int) -> list[ScheduledAinaExecution]:
        values = [item.model_copy(deep=True) for item in self._executions.values() if item.task_id == task_id]
        return sorted(values, key=lambda item: item.started_at, reverse=True)[:limit]
