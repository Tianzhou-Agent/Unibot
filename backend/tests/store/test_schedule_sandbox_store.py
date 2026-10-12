"""Scheduled tasks, sandboxes and their execution history in SQL, shared by several repository instances (replicas).

Runs on SQLite; concurrent writes are covered by ``test_schedule_sandbox_store_mysql.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tianzhou_agent_platform.aina.scheduler import (
    ScheduledAinaExecution,
    ScheduledAinaTaskCreate,
    ScheduledAinaTaskUpdate,
)
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.sandbox.models import SandboxExecution, SandboxRecord
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.repository import PersistentRepository
from tests.support.sqlite_store import sqlite_session_factory


class _SqlOnly:
    def __init__(self) -> None:
        self.session_factory = sqlite_session_factory()


def _replicas() -> tuple[PersistentRepository, PersistentRepository]:
    stores = StorageStores(mysql=_SqlOnly(), redis=None, nas=None)  # type: ignore[arg-type]
    return PersistentRepository(stores), PersistentRepository(stores)


def _schedule(user_id: str = "u1", **fields: object) -> ScheduledAinaTaskCreate:
    return ScheduledAinaTaskCreate.model_validate(
        {"aina_id": "aina.remote", "user_id": user_id, "tenant_id": "t1", "name": "digest", **fields}
    )


async def test_schedules_are_shared_and_filtered_by_owner() -> None:
    first, second = _replicas()
    mine = await first.create_scheduled_aina_task(_schedule(prompt="summarize"))
    theirs = await first.create_scheduled_aina_task(_schedule("u2"))

    assert await second.get_scheduled_aina_task(mine.id) == mine
    assert [task.id for task in await second.list_scheduled_aina_tasks(user_id="u1", tenant_id="t1")] == [mine.id]
    assert {task.id for task in await second.list_scheduled_aina_tasks()} == {mine.id, theirs.id}
    with pytest.raises(PlatformError):
        await second.get_scheduled_aina_task("schedule_missing")


async def test_only_enabled_due_tasks_are_listed_for_the_scheduler() -> None:
    first, second = _replicas()
    now = datetime.now(UTC)
    due = await first.create_scheduled_aina_task(_schedule())
    later = await first.create_scheduled_aina_task(_schedule())
    disabled = await first.create_scheduled_aina_task(_schedule())
    await first.put_scheduled_aina_task(due.model_copy(update={"next_run_at": now - timedelta(minutes=1)}))
    await first.put_scheduled_aina_task(later.model_copy(update={"next_run_at": now + timedelta(hours=1)}))
    await first.put_scheduled_aina_task(
        disabled.model_copy(update={"enabled": False, "next_run_at": now - timedelta(minutes=1)})
    )

    assert [task.id for task in await second.list_due_scheduled_aina_tasks(now)] == [due.id]


async def test_an_update_on_one_replica_recomputes_the_schedule_for_all() -> None:
    first, second = _replicas()
    task = await first.create_scheduled_aina_task(_schedule(interval_seconds=3600))

    updated = await second.update_scheduled_aina_task(
        task.id, ScheduledAinaTaskUpdate(interval_seconds=60, prompt="new prompt")
    )

    assert updated.next_run_at < task.next_run_at
    assert await first.get_scheduled_aina_task(task.id) == updated
    assert updated.input == {"message": "new prompt"}
    with pytest.raises(PlatformError):
        await first.update_scheduled_aina_task("schedule_missing", ScheduledAinaTaskUpdate(name="x"))


async def test_execution_history_is_newest_first_and_removed_with_its_task() -> None:
    first, second = _replicas()
    task = await first.create_scheduled_aina_task(_schedule())
    other = await first.create_scheduled_aina_task(_schedule())
    start = datetime(2026, 10, 1, tzinfo=UTC)

    def execution(index: int, task_id: str) -> ScheduledAinaExecution:
        return ScheduledAinaExecution(
            id=f"execution_{task_id}_{index}",
            task_id=task_id,
            aina_id="aina.remote",
            user_id="u1",
            tenant_id="t1",
            trigger="scheduled",
            call_id=f"call_{index}",
            node_id="n1",
            input={},
            started_at=start + timedelta(minutes=index),
        )

    for index in range(3):
        await first.put_scheduled_aina_execution(execution(index, task.id))
    await first.put_scheduled_aina_execution(execution(0, other.id))
    finished = execution(2, task.id).model_copy(update={"status": "succeeded", "result": {"ok": True}})
    await second.put_scheduled_aina_execution(finished)

    history = await second.list_scheduled_aina_executions(task.id, limit=2)
    assert [item.id for item in history] == [execution(2, task.id).id, execution(1, task.id).id]
    assert history[0] == finished

    await second.remove_scheduled_aina_task(task.id)
    assert await first.list_scheduled_aina_executions(task.id) == []
    assert len(await first.list_scheduled_aina_executions(other.id)) == 1
    with pytest.raises(PlatformError):
        await first.remove_scheduled_aina_task(task.id)


def _sandbox(sandbox_id: str, workspace_id: str | None = None) -> SandboxRecord:
    return SandboxRecord(
        id=sandbox_id,
        user_id="u1",
        tenant_id="t1",
        workspace_id=workspace_id,
        image="python:3.12",
        driver="local",
        runtime_name=f"runtime-{sandbox_id}",
        workspace="/workspace",
    )


def _run(execution_id: str, sandbox_id: str, workspace_id: str | None, minute: int) -> SandboxExecution:
    return SandboxExecution(
        id=execution_id,
        sandbox_id=sandbox_id,
        user_id="u1",
        tenant_id="t1",
        workspace_id=workspace_id,
        language="python",
        script="print(1)",
        working_directory=".",
        started_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minute),
    )


async def test_sandboxes_are_found_by_scope_on_any_replica() -> None:
    first, second = _replicas()
    standalone = await first.put_sandbox(_sandbox("sandbox_a"))
    scoped = await first.put_sandbox(_sandbox("sandbox_b", workspace_id="ws1"))

    assert await second.get_sandbox_for_actor(user_id="u1", tenant_id="t1") == standalone
    assert await second.get_sandbox_for_actor(user_id="u1", tenant_id="t1", workspace_id="ws1") == scoped
    with pytest.raises(PlatformError):
        await second.get_sandbox_for_actor(user_id="u1", tenant_id="t1", workspace_id="ws2")

    busy = await second.put_sandbox(standalone.model_copy(update={"status": "busy"}))
    assert (await first.get_sandbox_for_actor(user_id="u1", tenant_id="t1")).status == busy.status == "busy"

    with pytest.raises(PlatformError) as error:
        await first.put_sandbox(_sandbox("sandbox_other"))
    assert error.value.code == "CONFLICT"


async def test_sandbox_history_is_scoped_ordered_and_removed_with_its_sandbox() -> None:
    first, second = _replicas()
    await first.put_sandbox(_sandbox("sandbox_a"))
    await first.put_sandbox(_sandbox("sandbox_b", workspace_id="ws1"))
    for minute in range(3):
        await first.put_sandbox_execution(_run(f"run_a{minute}", "sandbox_a", None, minute))
    await first.put_sandbox_execution(_run("run_b0", "sandbox_b", "ws1", 0))
    completed = _run("run_a2", "sandbox_a", None, 2).model_copy(update={"status": "succeeded", "stdout": "1\n"})
    await second.put_sandbox_execution(completed)

    history = await second.list_sandbox_executions(user_id="u1", tenant_id="t1", limit=2)
    assert [item.id for item in history] == ["run_a2", "run_a1"]
    assert history[0] == completed
    assert [
        item.id for item in await second.list_sandbox_executions(user_id="u1", tenant_id="t1", workspace_id="ws1")
    ] == ["run_b0"]

    await second.remove_sandbox("sandbox_a")
    assert await first.list_sandbox_executions(user_id="u1", tenant_id="t1") == []
    assert len(await first.list_sandbox_executions(user_id="u1", tenant_id="t1", workspace_id="ws1")) == 1
    with pytest.raises(PlatformError):
        await first.remove_sandbox("sandbox_a")
