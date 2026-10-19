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
from tianzhou_agent_platform.conversations.models import Conversation
from tianzhou_agent_platform.conversations.schemas import ChatRequest
from tianzhou_agent_platform.core.errors import PlatformError, conflict

if TYPE_CHECKING:
    from tianzhou_agent_platform.aina.protocol.models import AinaRecord
    from tianzhou_agent_platform.services.chat import ChatService
    from tianzhou_agent_platform.store.memory_repository import InMemoryRepository

ScheduleType = Literal["interval", "cron"]


class ScheduledAinaTaskCreate(StrictModel):
    aina_id: str = ""
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

    @field_validator("aina_id", mode="before")
    @classmethod
    def normalize_target(cls, value: str | None) -> str:
        return "" if value is None else value

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
        if not self.aina_id:
            agent_message({"message": self.prompt} if self.prompt is not None else self.input)
        return self


class ScheduledAinaTaskUpdate(StrictModel):
    aina_id: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=160)
    schedule_type: ScheduleType | None = None
    interval_seconds: int | None = Field(default=None, ge=10, le=31_536_000)
    cron_expression: str | None = None
    timezone: str | None = None
    prompt: str | None = Field(default=None, min_length=1, max_length=20_000)
    input: dict[str, Any] | None = None
    enabled: bool | None = None

    @field_validator("aina_id", mode="before")
    @classmethod
    def normalize_target(cls, value: str | None) -> str:
        return "" if value is None else value

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
    last_status: Literal["never", "running", "succeeded", "failed", "approval_required"] = "never"
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
        if not self.aina_id:
            agent_message(self.input)
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
    status: Literal["running", "succeeded", "failed", "approval_required"] = "running"
    result: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime = Field(default_factory=utc_now)
    finished_at: datetime | None = None
    duration_ms: float | None = None


def agent_message(arguments: dict[str, Any]) -> str:
    message = arguments.get("message")
    if not isinstance(message, str) or not message.strip():
        raise ValueError("Agent schedules require a non-empty prompt or input.message")
    return message


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
        chat_service: ChatService | None = None,
        node_id: str | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self.repository = repository
        self.gateway = gateway
        self.chat_service = chat_service
        self.node_id = node_id or socket.gethostname()
        self.poll_seconds = poll_seconds
        self._stop = asyncio.Event()
        self._local_claims = repository._scheduled_aina_claims
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
        for task in await self.repository.list_due_scheduled_aina_tasks(current):
            claim_id = f"{task.id}:{task.next_run_at.isoformat()}"
            async with self._lease(
                "aina-schedule-lease",
                claim_id,
                ttl_seconds=max(60, task.interval_seconds),
            ) as acquired:
                if not acquired:
                    continue
                async with self._lease("aina-schedule-run", task.id, ttl_seconds=15 * 60) as run_acquired:
                    if not run_acquired:
                        continue
                    try:
                        latest = await self.repository.get_scheduled_aina_task(task.id)
                    except PlatformError as exc:
                        if exc.code == "RESOURCE_NOT_FOUND":
                            continue
                        raise
                    if not latest.enabled or latest.next_run_at != task.next_run_at or latest.next_run_at > current:
                        continue
                    await self._execute(
                        latest,
                        current,
                        claim_id,
                        trigger="scheduled",
                        advance_schedule=True,
                    )

    async def run_now(
        self, task_id: str, *, input_override: dict[str, Any] | None = None
    ) -> ScheduledAinaTask:
        claim_id = f"debug:{task_id}:{uuid4().hex}"
        async with self._lease(
            "aina-schedule-run",
            task_id,
            ttl_seconds=15 * 60,
        ) as acquired:
            if not acquired:
                raise conflict("This scheduled task is already running")
            task = await self.repository.get_scheduled_aina_task(task_id)
            if not task.aina_id:
                self._agent_message(task.input if input_override is None else input_override)
            return await self._execute(
                task,
                datetime.now(UTC),
                claim_id,
                trigger="manual",
                advance_schedule=False,
                input_override=input_override,
            )

    async def reconcile_approval(
        self,
        conversation_id: str,
        *,
        user_id: str,
        tenant_id: str,
        status: Literal["succeeded", "failed", "approval_required"],
        error: str | None,
        result: dict[str, Any] | None,
    ) -> None:
        """Carry a conversation approval outcome back to the schedule run that paused for it.

        The schedule stores the dispatch-time snapshot, so a run that asked for approval first appears as
        ``approval_required``. When the user confirms or denies it in any client, the task and its execution
        record should show the outcome instead of staying pending forever. Only the snapshot that still points
        at this conversation is updated; later scheduled runs are left alone.
        """
        finished = datetime.now(UTC)
        for task in await self.repository.list_scheduled_aina_tasks(user_id=user_id, tenant_id=tenant_id):
            snapshot = task.last_result or {}
            if task.last_status != "approval_required" or snapshot.get("conversation_id") != conversation_id:
                continue
            await self.repository.put_scheduled_aina_task(
                task.model_copy(
                    update={
                        "last_status": status,
                        "last_error": error,
                        "last_result": result,
                        "updated_at": finished,
                    },
                    deep=True,
                )
            )
            for execution in await self.repository.list_scheduled_aina_executions(task.id, limit=200):
                execution_snapshot = execution.result or {}
                if (
                    execution.status != "approval_required"
                    or execution_snapshot.get("conversation_id") != conversation_id
                ):
                    continue
                await self.repository.put_scheduled_aina_execution(
                    execution.model_copy(
                        update={
                            "status": status,
                            "error": error,
                            "result": result,
                            "finished_at": finished,
                            "duration_ms": max(
                                0.0, (finished - execution.started_at).total_seconds() * 1000
                            ),
                        },
                        deep=True,
                    )
                )

    async def validate_task(
        self,
        task: ScheduledAinaTask | ScheduledAinaTaskCreate,
        *,
        input_override: dict[str, Any] | None = None,
    ) -> AinaRecord | None:
        arguments = (
            input_override
            if input_override is not None
            else {"message": task.prompt} if task.prompt is not None else task.input
        )
        if not task.aina_id:
            self._agent_message(arguments)
            if self.chat_service is None:
                raise PlatformError("INVALID_REQUEST", "The Agent runtime is unavailable", status_code=422)
            return None
        record = await self.repository.get_aina(task.aina_id)
        if record.status != "registered":
            raise PlatformError("PERMISSION_DENIED", "AINA is disabled", status_code=403)
        if record.manifest.runtime.type in {"remote", "managed"}:
            installation = await self.repository.get_installation(
                tenant_id=task.tenant_id, user_id=task.user_id, aina_id=task.aina_id
            )
            if installation.status != "active":
                raise PlatformError("PERMISSION_DENIED", "AINA installation is disabled", status_code=403)
            if set(record.manifest.permissions) - set(installation.granted_permissions):
                raise PlatformError("PERMISSION_DENIED", "AINA permissions have not been granted", status_code=403)
        else:
            self._agent_message(arguments)
            if self.chat_service is None:
                raise PlatformError("INVALID_REQUEST", "The Agent runtime is unavailable", status_code=422)
            conversation = Conversation(
                id="schedule-validation",
                user_id=task.user_id,
                tenant_id=task.tenant_id,
                title=task.name,
            )
            capabilities = await self.chat_service.runner.catalog.available(conversation)
            if not any(item.kind == "aina" and item.capability_id == task.aina_id for item in capabilities.values()):
                raise PlatformError("PERMISSION_DENIED", "The selected AINA is unavailable", status_code=403)
        return record

    @staticmethod
    def _agent_message(arguments: dict[str, Any]) -> str:
        try:
            return agent_message(arguments)
        except ValueError as exc:
            raise PlatformError("INVALID_REQUEST", str(exc), status_code=422) from exc

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

        local_key = f"{namespace}:{key}"
        if stores is not None:
            result = await stores.redis.set_if_absent(
                namespace, key, {"node_id": self.node_id}, ttl_seconds=ttl_seconds
            )
            acquired = bool(result.written)
        else:
            acquired = local_key not in self._local_claims
            if acquired:
                self._local_claims.add(local_key)
        try:
            yield acquired
        finally:
            if acquired and stores is None:
                self._local_claims.discard(local_key)

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
        running = await self.repository.start_scheduled_aina_run(
            task.id,
            node_id=self.node_id,
            started_at=now,
            scheduled_for=task.next_run_at if trigger == "scheduled" else None,
        )
        if running is None:
            if trigger == "manual":
                raise conflict("This task was removed before it could start")
            return task
        task = running
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
        cancelled: asyncio.CancelledError | None = None
        try:
            record = await self.validate_task(task, input_override=execution_input)
            status: Literal["succeeded", "failed", "approval_required"]
            if record is None or record.manifest.runtime.type == "builtin":
                if self.chat_service is None:
                    raise PlatformError("INVALID_REQUEST", "The Agent runtime is unavailable", status_code=422)
                response = await self.chat_service.run_turn(
                    ChatRequest(
                        message=self._agent_message(execution_input),
                        user_id=task.user_id,
                        tenant_id=task.tenant_id,
                        capability=f"aina:{task.aina_id}" if task.aina_id else None,
                    )
                )
                status = (
                    "succeeded"
                    if response.status == "completed"
                    else "approval_required" if response.status == "approval_required" else "failed"
                )
                error = response.content if status == "failed" else None
                result = response.model_dump(mode="json")
            else:
                installation = await self.repository.get_installation(
                    tenant_id=task.tenant_id, user_id=task.user_id, aina_id=task.aina_id
                )
                remote_response, _ = await self.gateway.invoke_aina(
                    record.manifest,
                    installation,
                    arguments=execution_input,
                    call_id=claim_id,
                    conversation_id=task.id,
                    trace_id=f"scheduled_{uuid4().hex}",
                    available_tools=[item.id for item in record.manifest.capabilities.tools],
                )
                status = (
                    "succeeded"
                    if remote_response.status == "completed"
                    else "approval_required" if remote_response.status == "approval_required" else "failed"
                )
                error = None if status != "failed" else f"AINA returned {remote_response.status}"
                result = remote_response.model_dump(mode="json")
        except PlatformError as exc:
            status = "failed"
            error = exc.user_message or exc.message
            result = None
        except asyncio.CancelledError as exc:
            cancelled = exc
            status = "failed"
            error = "The scheduled task was interrupted. Please retry."
            result = None
        except Exception:  # execution failures are persisted for operators
            status = "failed"
            error = "The scheduled task failed unexpectedly."
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
        finished_task = await self.repository.finish_scheduled_aina_run(
            task.id,
            status=status,
            error=error,
            result=result,
            finished_at=finished,
            advance_schedule=advance_schedule,
        )
        if cancelled is not None:
            raise cancelled
        # A task deleted while it ran stays deleted; report the run on the task as it was.
        return finished_task or running or task
