from __future__ import annotations

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal, cast
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-untyped]
from apscheduler.triggers.interval import IntervalTrigger  # type: ignore[import-untyped]
from croniter import croniter  # type: ignore[import-untyped]
from pydantic import Field, field_validator, model_validator

from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.core.base import StrictModel, utc_now
from tianzhou_agent_platform.core.errors import conflict

if TYPE_CHECKING:
    from tianzhou_agent_platform.core.repository import InMemoryRepository

ScheduleType = Literal["interval", "cron"]


class ScheduledAinaTaskCreate(StrictModel):
    aina_id: str
    user_id: str = "anonymous"
    tenant_id: str = "default"
    name: str = Field(min_length=1, max_length=160)
    schedule_type: ScheduleType = "interval"
    interval_seconds: int = Field(default=3600, ge=10, le=31_536_000)
    cron_expression: str | None = None
    timezone: str = "UTC"
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    input: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_cron(value)
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        _timezone(value)
        return value

    @model_validator(mode="after")
    def require_cron_expression(self) -> "ScheduledAinaTaskCreate":
        if self.schedule_type == "cron" and self.cron_expression is None:
            raise ValueError("cron_expression is required for cron schedules")
        return self


class ScheduledAinaTaskUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    schedule_type: ScheduleType | None = None
    interval_seconds: int | None = Field(default=None, ge=10, le=31_536_000)
    cron_expression: str | None = None
    timezone: str | None = None
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    input: dict[str, Any] | None = None
    enabled: bool | None = None

    @field_validator("cron_expression")
    @classmethod
    def validate_cron(cls, value: str | None) -> str | None:
        if value is not None:
            _validate_cron(value)
        return value

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            _timezone(value)
        return value


class ScheduledAinaDebugRequest(StrictModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    input: dict[str, Any] | None = None

    def invocation_input(self) -> dict[str, Any] | None:
        if self.prompt is not None:
            return {"message": self.prompt}
        return self.input


class ScheduledAinaTask(StrictModel):
    id: str = Field(default_factory=lambda: f"schedule_{uuid4().hex}")
    aina_id: str
    user_id: str
    tenant_id: str
    name: str
    schedule_type: ScheduleType = "interval"
    interval_seconds: int = 3600
    cron_expression: str | None = None
    timezone: str = "UTC"
    prompt: str | None = None
    input: dict[str, Any]
    enabled: bool = True
    next_run_at: datetime = Field(default_factory=utc_now)
    last_run_at: datetime | None = None
    last_status: Literal["never", "running", "succeeded", "failed"] = "never"
    last_node_id: str | None = None
    last_error: str | None = None
    last_result: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_schedule(self) -> "ScheduledAinaTask":
        _timezone(self.timezone)
        if self.schedule_type == "cron":
            if self.cron_expression is None:
                raise ValueError("cron_expression is required for cron schedules")
            _validate_cron(self.cron_expression)
        return self


class ScheduledAinaExecution(StrictModel):
    id: str = Field(default_factory=lambda: f"execution_{uuid4().hex}")
    task_id: str
    aina_id: str
    user_id: str
    tenant_id: str
    trigger: Literal["scheduled", "manual"]
    scheduled_for: datetime | None = None
    call_id: str
    node_id: str
    input: dict[str, Any]
    status: Literal["running", "succeeded", "failed"] = "running"
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    duration_ms: float | None = None


def next_scheduled_run(task: ScheduledAinaTask, *, after: datetime | None = None) -> datetime:
    current = after or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if task.schedule_type == "interval":
        return current.astimezone(UTC) + timedelta(seconds=task.interval_seconds)
    if task.cron_expression is None:  # guarded by model validation
        raise ValueError("cron_expression is required for cron schedules")
    zone = _timezone(task.timezone)
    next_run = cast(datetime, croniter(task.cron_expression, current.astimezone(zone)).get_next(datetime))
    if next_run.tzinfo is None:
        next_run = next_run.replace(tzinfo=zone)
    return next_run.astimezone(UTC)


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {value}") from exc


def _validate_cron(expression: str) -> None:
    if len(expression.strip().split()) != 5 or not croniter.is_valid(expression):
        raise ValueError("Cron expression must be a valid five-field crontab expression")


class AinaScheduler:
    def __init__(
        self,
        repository: InMemoryRepository,
        gateway: RemoteCapabilityGateway,
        *,
        node_id: str | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self.repository = repository
        self.gateway = gateway
        self.node_id = node_id or socket.gethostname()
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._local_claims: set[str] = set()
        self._driver: AsyncIOScheduler | None = None

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        self._driver = AsyncIOScheduler(timezone=UTC)
        self._driver.add_job(
            self.tick,
            trigger=IntervalTrigger(seconds=self.poll_seconds, timezone=UTC),
            id=f"aina-scheduler-poll-{self.node_id}",
            max_instances=1,
            coalesce=True,
            next_run_time=datetime.now(UTC),
        )
        self._driver.start()
        try:
            await self._stop.wait()
        finally:
            self._driver.shutdown(wait=True)
            self._driver = None

    async def tick(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        for task in await self.repository.list_scheduled_aina_tasks():
            if not task.enabled or task.next_run_at > current:
                continue
            claim_id = f"{task.id}:{task.next_run_at.isoformat()}"
            async with self._lease(
                "aina-schedule-lease",
                claim_id,
                ttl_seconds=max(60, task.interval_seconds),
            ) as acquired:
                if not acquired:
                    continue
                await self._execute(
                    task,
                    current,
                    claim_id,
                    trigger="scheduled",
                    advance_schedule=True,
                )

    async def run_now(
        self, task_id: str, *, input_override: dict[str, Any] | None = None
    ) -> ScheduledAinaTask:
        task = await self.repository.get_scheduled_aina_task(task_id)
        claim_id = f"debug:{task_id}:{uuid4().hex}"
        async with self._lease(
            "aina-schedule-debug",
            task_id,
            ttl_seconds=15 * 60,
        ) as acquired:
            if not acquired:
                raise conflict("This scheduled AINA task is already running")
            return await self._execute(
                task,
                datetime.now(UTC),
                claim_id,
                trigger="manual",
                advance_schedule=False,
                input_override=input_override,
            )

    @asynccontextmanager
    async def _lease(
        self,
        namespace: str,
        key: str,
        *,
        ttl_seconds: int,
    ) -> AsyncIterator[bool]:
        stores = getattr(self.repository, "stores", None)
        if stores is not None and hasattr(stores.redis, "lease"):
            async with stores.redis.lease(namespace, key, ttl_seconds=ttl_seconds) as acquired:
                yield acquired
            return

        if namespace == "aina-schedule-debug":
            acquired = await self._claim_manual(key)
        else:
            acquired = await self._claim(key, ttl_seconds)
        try:
            yield acquired
        finally:
            if acquired and stores is None:
                self._local_claims.discard(key)
                self._local_claims.discard(f"debug:{key}")

    async def _claim(self, claim_id: str, interval_seconds: int) -> bool:
        stores = getattr(self.repository, "stores", None)
        if stores is not None:
            result = await stores.redis.set_if_absent(
                "aina-schedule-lease",
                claim_id,
                {"node_id": self.node_id},
                ttl_seconds=max(60, interval_seconds),
            )
            return cast(bool, result.written)
        if claim_id in self._local_claims:
            return False
        self._local_claims.add(claim_id)
        return True

    async def _claim_manual(self, task_id: str) -> bool:
        stores = getattr(self.repository, "stores", None)
        if stores is not None:
            result = await stores.redis.set_if_absent(
                "aina-schedule-debug",
                task_id,
                {"node_id": self.node_id},
                ttl_seconds=15 * 60,
            )
            return cast(bool, result.written)
        claim_id = f"debug:{task_id}"
        if claim_id in self._local_claims:
            return False
        self._local_claims.add(claim_id)
        return True

    async def _execute(
        self,
        task: ScheduledAinaTask,
        now: datetime,
        claim_id: str,
        *,
        trigger: Literal["scheduled", "manual"],
        advance_schedule: bool,
        input_override: dict[str, Any] | None = None,
    ) -> ScheduledAinaTask:
        execution_input = task.input if input_override is None else input_override
        execution = ScheduledAinaExecution(
            task_id=task.id,
            aina_id=task.aina_id,
            user_id=task.user_id,
            tenant_id=task.tenant_id,
            trigger=trigger,
            scheduled_for=task.next_run_at if trigger == "scheduled" else None,
            call_id=claim_id,
            node_id=self.node_id,
            input=execution_input,
            started_at=now,
        )
        await self.repository.put_scheduled_aina_execution(execution)
        running = task.model_copy(
            update={"last_status": "running", "last_node_id": self.node_id, "updated_at": now}
        )
        await self.repository.put_scheduled_aina_task(running)
        try:
            record = await self.repository.get_aina(task.aina_id)
            installation = await self.repository.get_installation(
                tenant_id=task.tenant_id, user_id=task.user_id, aina_id=task.aina_id
            )
            response, _ = await self.gateway.invoke_aina(
                record.manifest,
                installation,
                arguments=execution_input,
                call_id=claim_id,
                conversation_id=task.id,
                trace_id=f"scheduled_{uuid4().hex}",
                available_tools=[item.id for item in record.manifest.capabilities.tools],
            )
            status: Literal["succeeded", "failed"] = (
                "succeeded" if response.status == "completed" else "failed"
            )
            error = None if status == "succeeded" else f"AINA returned {response.status}"
            result = response.model_dump(mode="json")
        except Exception as exc:  # execution failures are persisted for operators
            status = "failed"
            error = str(exc)
            result = None
        finished = datetime.now(UTC)
        await self.repository.put_scheduled_aina_execution(
            execution.model_copy(
                update={
                    "status": status,
                    "result": result,
                    "error": error,
                    "finished_at": finished,
                    "duration_ms": max(0.0, (finished - now).total_seconds() * 1000),
                },
                deep=True,
            )
        )
        updated = running.model_copy(
            update={
                "next_run_at": next_scheduled_run(task, after=finished) if advance_schedule else task.next_run_at,
                "last_run_at": finished,
                "last_status": status,
                "last_error": error,
                "last_result": result,
                "updated_at": finished,
            },
            deep=True,
        )
        return await self.repository.put_scheduled_aina_task(updated)
