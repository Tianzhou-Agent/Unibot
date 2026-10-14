from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from tianzhou_agent_platform.aina.scheduler import (
    AinaScheduler,
    ScheduledAinaTask,
    ScheduledAinaTaskCreate,
    ScheduledAinaTaskUpdate,
    next_scheduled_run,
)
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.conversations.schemas import ChatResponse
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository
from tianzhou_agent_platform.main import create_app
from tests.support.fake_llm import ScriptedLLM


class SharedRedis:
    def __init__(self) -> None:
        self.keys: set[tuple[str, str]] = set()

    async def set_if_absent(self, namespace: str, key: str, value: object, *, ttl_seconds: int):
        marker = (namespace, key)
        written = marker not in self.keys
        self.keys.add(marker)
        return SimpleNamespace(written=written)

    @asynccontextmanager
    async def lease(self, namespace: str, key: str, *, ttl_seconds: int):
        marker = (namespace, key)
        acquired = marker not in self.keys
        if acquired:
            self.keys.add(marker)
        try:
            yield acquired
        finally:
            if acquired:
                self.keys.remove(marker)


def test_distributed_scheduler_lease_has_a_single_winner() -> None:
    shared_redis = SharedRedis()
    first_repository = InMemoryRepository()
    second_repository = InMemoryRepository()
    first_repository.stores = SimpleNamespace(redis=shared_redis)  # type: ignore[attr-defined]
    second_repository.stores = SimpleNamespace(redis=shared_redis)  # type: ignore[attr-defined]
    first = AinaScheduler(first_repository, SimpleNamespace(), node_id="node-a")  # type: ignore[arg-type]
    second = AinaScheduler(second_repository, SimpleNamespace(), node_id="node-b")  # type: ignore[arg-type]

    async def claim_once(scheduler: AinaScheduler) -> bool:
        async with scheduler._lease("aina-schedule-lease", "task:run", ttl_seconds=60) as acquired:
            await asyncio.sleep(0)
            return acquired

    async def claim() -> list[bool]:
        return list(await asyncio.gather(claim_once(first), claim_once(second)))

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


def test_cron_schedule_uses_the_configured_timezone() -> None:
    task = ScheduledAinaTask(
        aina_id="report",
        user_id="anonymous",
        tenant_id="default",
        name="Weekday report",
        schedule_type="cron",
        cron_expression="0 9 * * 1-5",
        timezone="Asia/Shanghai",
        input={},
    )

    next_run = next_scheduled_run(task, after=datetime(2026, 7, 17, 1, 0, tzinfo=UTC))

    assert next_run == datetime(2026, 7, 20, 1, 0, tzinfo=UTC)


def test_invalid_cron_expression_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ScheduledAinaTaskCreate(
            aina_id="report",
            name="Broken schedule",
            schedule_type="cron",
            cron_expression="not-a-cron",
        )


def test_schedule_can_be_created_and_debugged_through_the_api() -> None:
    invoked: list[dict[str, object]] = []

    async def remote(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/describe"):
            return httpx.Response(200, json={"protocol_version": "1.0", "capabilities": {}})
        if request.url.path.endswith("/health"):
            return httpx.Response(200, json={"status": "healthy"})
        if request.url.path.endswith("/invoke"):
            payload = json.loads(request.content)
            invoked.append(payload)
            return httpx.Response(
                200,
                json={
                    "request_id": payload["request_id"],
                    "status": "completed",
                    "outputs": [{"type": "json", "content": payload["input"]}],
                    "trace_id": payload["trace"]["trace_id"],
                },
            )
        return httpx.Response(404)

    manifest = {
        "protocol_version": "1.0",
        "aina": {
            "id": "com.example.scheduled",
            "name": "定时测试 AINA",
            "version": "1.0.0",
            "description": "用于验证中文定时任务输入与输出。",
            "publisher": {"id": "tests", "name": "Tests"},
        },
        "runtime": {"type": "remote", "endpoint": "https://aina.invalid/runtime"},
        "capabilities": {"skills": [], "tools": [], "ui": [], "events": []},
        "authentication": {"type": "none"},
    }
    settings = AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
    )
    capability_client = httpx.AsyncClient(transport=httpx.MockTransport(remote))
    with TestClient(
        create_app(
            settings=settings,
            llm=ScriptedLLM([]),
            capability_http_client=capability_client,
        )
    ) as client:
        registered = client.post("/ainas", json=manifest)
        assert registered.status_code == 201
        assert registered.json()["manifest"]["aina"]["name"] == "定时测试 AINA"
        assert registered.json()["manifest"]["aina"]["description"] == "用于验证中文定时任务输入与输出。"
        assert client.post("/ainas/com.example.scheduled/install", json={}).status_code == 200
        created = client.post(
            "/aina-schedules",
            json={
                "aina_id": "com.example.scheduled",
                "name": "每日晨报",
                "schedule_type": "cron",
                "cron_expression": "0 9 * * *",
                "timezone": "Asia/Shanghai",
                "prompt": "生成今天的自动晨报",
            },
        )
        original_next_run = created.json()["next_run_at"]
        debugged = client.post(
            f"/aina-schedules/{created.json()['id']}/run",
            json={"prompt": "生成晨报预览并标出异常项"},
        )
        executions = client.get(
            f"/aina-schedules/{created.json()['id']}/executions"
        )

    assert created.status_code == 201
    assert debugged.status_code == 200
    assert debugged.json()["last_status"] == "succeeded"
    assert debugged.json()["next_run_at"] == original_next_run
    assert created.json()["name"] == "每日晨报"
    assert debugged.json()["prompt"] == "生成今天的自动晨报"
    assert invoked[-1]["input"] == {"message": "生成晨报预览并标出异常项"}
    assert executions.status_code == 200
    assert len(executions.json()) == 1
    execution = executions.json()[0]
    assert execution["trigger"] == "manual"
    assert execution["status"] == "succeeded"
    assert execution["input"] == {"message": "生成晨报预览并标出异常项"}
    assert execution["node_id"]
    assert execution["duration_ms"] >= 0


class ChangedWhileRunning(InMemoryRepository):
    """Applies ``during_run`` to the task while the scheduler is executing it."""

    def __init__(self) -> None:
        super().__init__()
        self.during_run = None

    async def get_aina(self, aina_id: str):  # type: ignore[no-untyped-def]
        await self.during_run()
        return await super().get_aina(aina_id)


def test_a_run_keeps_edits_made_while_it_ran_and_does_not_restore_a_deleted_task() -> None:
    repository = ChangedWhileRunning()
    scheduler = AinaScheduler(repository, SimpleNamespace(), node_id="node-a")  # type: ignore[arg-type]

    async def run() -> tuple[ScheduledAinaTask, list[ScheduledAinaTask]]:
        edited = await repository.create_scheduled_aina_task(
            ScheduledAinaTaskCreate(aina_id="report", name="Daily report", interval_seconds=60)
        )
        repository.during_run = lambda: repository.update_scheduled_aina_task(
            edited.id, ScheduledAinaTaskUpdate(enabled=False, name="Paused report")
        )
        await scheduler.tick(now=edited.next_run_at)
        stored = await repository.get_scheduled_aina_task(edited.id)

        deleted = await repository.create_scheduled_aina_task(
            ScheduledAinaTaskCreate(aina_id="report", name="Removed report", interval_seconds=60)
        )
        repository.during_run = lambda: repository.remove_scheduled_aina_task(deleted.id)
        await scheduler.run_now(deleted.id)
        return stored, await repository.list_scheduled_aina_tasks()

    stored, remaining = asyncio.run(run())
    assert (stored.enabled, stored.name) == (False, "Paused report")
    assert stored.last_status == "failed"  # the AINA does not exist; the run itself is not under test
    assert [task.name for task in remaining] == ["Paused report"]


def test_a_released_occurrence_lease_does_not_run_a_stale_snapshot_twice() -> None:
    class StaleSnapshotRepository(InMemoryRepository):
        snapshot: list[ScheduledAinaTask]

        async def list_due_scheduled_aina_tasks(self, now: datetime) -> list[ScheduledAinaTask]:
            return self.snapshot

    repository = StaleSnapshotRepository()
    repository.stores = SimpleNamespace(redis=SharedRedis())  # type: ignore[attr-defined]
    calls: list[str] = []

    async def agent(request):  # type: ignore[no-untyped-def]
        calls.append(request.message)
        return ChatResponse(
            conversation_id="conversation-test", content="Done", status="completed", trace_id="trace-test", iterations=1
        )

    first = AinaScheduler(repository, SimpleNamespace(), chat_service=SimpleNamespace(run_turn=agent))  # type: ignore[arg-type]
    second = AinaScheduler(repository, SimpleNamespace(), chat_service=SimpleNamespace(run_turn=agent))  # type: ignore[arg-type]

    async def run() -> None:
        task = await repository.create_scheduled_aina_task(ScheduledAinaTaskCreate(name="Agent task", prompt="Summarize"))
        task = await repository.put_scheduled_aina_task(
            task.model_copy(update={"next_run_at": datetime.now(UTC) - timedelta(seconds=1)})
        )
        repository.snapshot = [task]
        await first.tick(now=task.next_run_at)
        await second.tick(now=task.next_run_at)
        assert len(await repository.list_scheduled_aina_executions(task.id)) == 1

    asyncio.run(run())
    assert calls == ["Summarize"]


@pytest.mark.parametrize("first_trigger", ["manual", "scheduled"])
@pytest.mark.parametrize("distributed", [False, True])
def test_manual_and_scheduled_runs_share_a_task_lease(first_trigger: str, distributed: bool) -> None:
    repository = InMemoryRepository()
    if distributed:
        repository.stores = SimpleNamespace(redis=SharedRedis())  # type: ignore[attr-defined]

    async def run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls: list[str] = []

        async def agent(request):  # type: ignore[no-untyped-def]
            calls.append(request.message)
            started.set()
            await release.wait()
            return ChatResponse(
                conversation_id="conversation-test", content="Done", status="completed", trace_id="trace-test", iterations=1
            )

        first = AinaScheduler(repository, SimpleNamespace(), chat_service=SimpleNamespace(run_turn=agent))  # type: ignore[arg-type]
        second = AinaScheduler(repository, SimpleNamespace(), chat_service=SimpleNamespace(run_turn=agent))  # type: ignore[arg-type]
        task = await repository.create_scheduled_aina_task(ScheduledAinaTaskCreate(name="Agent task", prompt="Summarize"))
        active = asyncio.create_task(
            first.run_now(task.id) if first_trigger == "manual" else first.tick(now=task.next_run_at)
        )
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            await second.tick(now=task.next_run_at)
            with pytest.raises(PlatformError) as error:
                await second.run_now(task.id)
            assert error.value.status_code == 409
            assert len(await repository.list_scheduled_aina_executions(task.id)) == 1
        finally:
            release.set()
            await active

        assert calls == ["Summarize"]
        if first_trigger == "manual":
            assert (await repository.get_scheduled_aina_task(task.id)).next_run_at == task.next_run_at
        # A completed run releases the shared lease, allowing a later manual run.
        await second.run_now(task.id)
        assert calls == ["Summarize", "Summarize"]

    asyncio.run(run())


def test_a_disabled_task_in_a_due_snapshot_is_not_executed() -> None:
    class DisabledAfterSnapshot(InMemoryRepository):
        async def list_due_scheduled_aina_tasks(self, now: datetime) -> list[ScheduledAinaTask]:
            snapshot = await super().list_due_scheduled_aina_tasks(now)
            for task in snapshot:
                await self.update_scheduled_aina_task(task.id, ScheduledAinaTaskUpdate(enabled=False))
            return snapshot

    repository = DisabledAfterSnapshot()
    scheduler = AinaScheduler(repository, SimpleNamespace())  # type: ignore[arg-type]

    async def run() -> None:
        task = await repository.create_scheduled_aina_task(ScheduledAinaTaskCreate(name="Agent task", prompt="Summarize"))
        await scheduler.tick(now=task.next_run_at)
        assert await repository.list_scheduled_aina_executions(task.id) == []
        assert (await repository.get_scheduled_aina_task(task.id)).last_status == "never"

    asyncio.run(run())


@pytest.mark.parametrize("change", ["pause", "reschedule", "delete", "input"])
def test_changes_between_due_snapshot_and_atomic_start_are_respected(change: str) -> None:
    class ChangedBeforeStart(InMemoryRepository):
        async def start_scheduled_aina_run(self, task_id: str, **kwargs):  # type: ignore[no-untyped-def]
            if change == "delete":
                await self.remove_scheduled_aina_task(task_id)
            else:
                patch = {
                    "pause": ScheduledAinaTaskUpdate(enabled=False),
                    "reschedule": ScheduledAinaTaskUpdate(interval_seconds=60),
                    "input": ScheduledAinaTaskUpdate(prompt="Updated input"),
                }[change]
                await self.update_scheduled_aina_task(task_id, patch)
            return await super().start_scheduled_aina_run(task_id, **kwargs)

    repository = ChangedBeforeStart()
    calls: list[str] = []

    async def agent(request):  # type: ignore[no-untyped-def]
        calls.append(request.message)
        return ChatResponse(
            conversation_id="conversation-test", content="Done", status="completed", trace_id="trace-test", iterations=1
        )

    scheduler = AinaScheduler(repository, SimpleNamespace(), chat_service=SimpleNamespace(run_turn=agent))  # type: ignore[arg-type]

    async def run() -> None:
        task = await repository.create_scheduled_aina_task(ScheduledAinaTaskCreate(name="Agent task", prompt="Original"))
        await scheduler.tick(now=task.next_run_at)
        history = await repository.list_scheduled_aina_executions(task.id)
        if change == "input":
            assert calls == ["Updated input"]
            assert history[0].input == {"message": "Updated input"}
        else:
            assert calls == []
            assert history == []

    asyncio.run(run())


@pytest.mark.parametrize("outcome", ["failed", "stopped", "known_error", "cancelled"])
def test_noncompleted_agent_runs_are_recorded_as_failed(outcome: str) -> None:
    repository = InMemoryRepository()

    async def agent(request):  # type: ignore[no-untyped-def]
        if outcome == "known_error":
            raise PlatformError("MODEL_ERROR", "private provider details", user_message="A safe provider error")
        if outcome == "cancelled":
            raise asyncio.CancelledError()
        return ChatResponse(
            conversation_id="conversation-test",
            content="The run did not complete",
            status=outcome,
            trace_id="trace-test",
            iterations=1,
        )

    scheduler = AinaScheduler(repository, SimpleNamespace(), chat_service=SimpleNamespace(run_turn=agent))  # type: ignore[arg-type]

    async def run() -> None:
        task = await repository.create_scheduled_aina_task(ScheduledAinaTaskCreate(name="Agent task", prompt="Summarize"))
        if outcome == "cancelled":
            with pytest.raises(asyncio.CancelledError):
                await scheduler.run_now(task.id)
        else:
            await scheduler.run_now(task.id)
        stored = await repository.get_scheduled_aina_task(task.id)
        execution = (await repository.list_scheduled_aina_executions(task.id))[0]
        assert stored.last_status == execution.status == "failed"
        assert stored.last_error == execution.error
        assert execution.finished_at is not None
        if outcome == "known_error":
            assert execution.error == "A safe provider error"
            assert "private provider details" not in stored.model_dump_json() + execution.model_dump_json()
        elif outcome in {"failed", "stopped"}:
            assert stored.last_result["status"] == outcome

    asyncio.run(run())
