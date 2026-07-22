from __future__ import annotations

import asyncio
import socket
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

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
            _parse_cron(value)
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
            _parse_cron(value)
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
            _parse_cron(self.cron_expression)
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


@dataclass(frozen=True)
class _CronSchedule:
    minute: set[int]
    hour: set[int]
    day: set[int]
    month: set[int]
    weekday: set[int]
    day_wildcard: bool
    weekday_wildcard: bool

    def matches(self, candidate: datetime) -> bool:
        cron_weekday = (candidate.weekday() + 1) % 7
        day_matches = candidate.day in self.day
        weekday_matches = cron_weekday in self.weekday
        if not self.day_wildcard and not self.weekday_wildcard:
            calendar_matches = day_matches or weekday_matches
        else:
            calendar_matches = day_matches and weekday_matches
        return (
            candidate.minute in self.minute
            and candidate.hour in self.hour
            and candidate.month in self.month
            and calendar_matches
        )


def next_scheduled_run(task: ScheduledAinaTask, *, after: datetime | None = None) -> datetime:
    current = after or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if task.schedule_type == "interval":
        return current.astimezone(UTC) + timedelta(seconds=task.interval_seconds)
    if task.cron_expression is None:  # guarded by model validation
        raise ValueError("cron_expression is required for cron schedules")
    schedule = _parse_cron(task.cron_expression)
    zone = _timezone(task.timezone)
    candidate = current.astimezone(zone).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60 * 5):
        if schedule.matches(candidate):
            return candidate.astimezone(UTC)
        candidate += timedelta(minutes=1)
    raise ValueError("Cron expression has no matching time in the next five years")


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {value}") from exc


def _parse_cron(expression: str) -> _CronSchedule:
    fields = expression.strip().split()
    if len(fields) != 5:
        raise ValueError("Cron expression must contain five fields: minute hour day month weekday")
    minute = _parse_cron_field(fields[0], 0, 59, "minute")
    hour = _parse_cron_field(fields[1], 0, 23, "hour")
    day = _parse_cron_field(fields[2], 1, 31, "day")
    month = _parse_cron_field(fields[3], 1, 12, "month")
    weekday = _parse_cron_field(fields[4], 0, 7, "weekday")
    if 7 in weekday:
        weekday.remove(7)
        weekday.add(0)
    return _CronSchedule(
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        weekday=weekday,
        day_wildcard=fields[2] == "*",
        weekday_wildcard=fields[4] == "*",
    )


def _parse_cron_field(value: str, minimum: int, maximum: int, label: str) -> set[int]:
    result: set[int] = set()
    for item in value.split(","):
        base, separator, step_text = item.partition("/")
        if separator:
            try:
                step = int(step_text)
            except ValueError as exc:
                raise ValueError(f"Invalid {label} step: {step_text}") from exc
            if step <= 0:
                raise ValueError(f"Invalid {label} step: {step_text}")
        else:
            step = 1
        if base == "*":
            start, end = minimum, maximum
        elif "-" in base:
            start_text, end_text = base.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise ValueError(f"Invalid {label} range: {base}") from exc
        else:
            try:
                start = end = int(base)
            except ValueError as exc:
                raise ValueError(f"Invalid {label} value: {base}") from exc
        if start < minimum or end > maximum or start > end:
            raise ValueError(f"Invalid {label} range: {base}")
        result.update(range(start, end + 1, step))
    if not result:
        raise ValueError(f"Cron {label} field cannot be empty")
    return result


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

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        while not self._stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_seconds)
            except TimeoutError:
                pass

    async def tick(self, *, now: datetime | None = None) -> None:
        current = now or datetime.now(UTC)
        for task in await self.repository.list_scheduled_aina_tasks():
            if not task.enabled or task.next_run_at > current:
                continue
            claim_id = f"{task.id}:{task.next_run_at.isoformat()}"
            if not await self._claim(claim_id, task.interval_seconds):
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
        if not await self._claim_manual(task_id):
            raise conflict("This scheduled AINA task is already running")
        try:
            return await self._execute(
                task,
                datetime.now(UTC),
                claim_id,
                trigger="manual",
                advance_schedule=False,
                input_override=input_override,
            )
        finally:
            await self._release_manual(task_id)

    async def _claim(self, claim_id: str, interval_seconds: int) -> bool:
        stores = getattr(self.repository, "stores", None)
        if stores is not None:
            result = await stores.redis.set_if_absent(
                "aina-schedule-lease",
                claim_id,
                {"node_id": self.node_id},
                ttl_seconds=max(60, interval_seconds),
            )
            return result.written
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
            return result.written
        claim_id = f"debug:{task_id}"
        if claim_id in self._local_claims:
            return False
        self._local_claims.add(claim_id)
        return True

    async def _release_manual(self, task_id: str) -> None:
        stores = getattr(self.repository, "stores", None)
        if stores is not None:
            await stores.redis.delete("aina-schedule-debug", task_id)
        self._local_claims.discard(f"debug:{task_id}")

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
