from __future__ import annotations

import asyncio
import socket
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

from pydantic import Field

from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.core.base import StrictModel, utc_now
if TYPE_CHECKING:
    from tianzhou_agent_platform.core.repository import InMemoryRepository


class ScheduledAinaTaskCreate(StrictModel):
    aina_id: str
    user_id: str = "anonymous"
    tenant_id: str = "default"
    name: str = Field(min_length=1, max_length=160)
    interval_seconds: int = Field(ge=10, le=31_536_000)
    input: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class ScheduledAinaTaskUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    interval_seconds: int | None = Field(default=None, ge=10, le=31_536_000)
    input: dict[str, Any] | None = None
    enabled: bool | None = None


class ScheduledAinaTask(StrictModel):
    id: str = Field(default_factory=lambda: f"schedule_{uuid4().hex}")
    aina_id: str
    user_id: str
    tenant_id: str
    name: str
    interval_seconds: int
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
            await self._execute(task, current, claim_id)

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

    async def _execute(self, task: ScheduledAinaTask, now: datetime, claim_id: str) -> None:
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
                arguments=task.input,
                call_id=claim_id,
                conversation_id=task.id,
                trace_id=f"scheduled_{uuid4().hex}",
                available_tools=[item.id for item in record.manifest.capabilities.tools],
            )
            status: Literal["succeeded", "failed"] = "succeeded" if response.status == "completed" else "failed"
            error = None if status == "succeeded" else f"AINA returned {response.status}"
            result = response.model_dump(mode="json")
        except Exception as exc:  # execution failures are persisted for operators
            status = "failed"
            error = str(exc)
            result = None
        finished = datetime.now(UTC)
        updated = running.model_copy(
            update={
                "next_run_at": max(task.next_run_at + timedelta(seconds=task.interval_seconds), finished),
                "last_run_at": finished,
                "last_status": status,
                "last_error": error,
                "last_result": result,
                "updated_at": finished,
            },
            deep=True,
        )
        await self.repository.put_scheduled_aina_task(updated)
