"""Scheduled AINA protocol models and pure schedule computation.

Lives in the protocol layer so ``core`` repositories can store schedule
records without importing the scheduler implementation.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter  # type: ignore[import-untyped]
from pydantic import Field, field_validator, model_validator

from tianzhou_agent_platform.core.base import StrictModel, utc_now

ScheduleType = Literal["interval", "cron"]


def _timezone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown timezone: {value}") from exc


def _validate_cron(expression: str) -> None:
    if len(expression.strip().split()) != 5 or not croniter.is_valid(expression):
        raise ValueError("Cron expression must be a valid five-field crontab expression")


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
