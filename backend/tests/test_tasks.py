from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.base import utc_now
from tianzhou_agent_platform.core.conversation import ConversationCreate
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.tasks.models import (
    GateResult,
    TaskCreateRequest,
    TaskDeleteRequest,
    TaskUpdateRequest,
)
from tianzhou_agent_platform.tasks.service import TaskEventBroker, TaskService, derive_parent_status
from tianzhou_agent_platform.tasks.store import InMemorySessionTaskStore, MutationDecision
from tianzhou_agent_platform.tasks.store import session_task_meta_table, session_tasks_table
from tests.support.fake_llm import ScriptedLLM, assistant, call_first_tool


async def _service(*, gate: object | None = None) -> tuple[TaskService, str]:
    repository = InMemoryRepository()
    conversation = await repository.create_conversation(ConversationCreate())
    service = TaskService(
        repository,
        InMemorySessionTaskStore(),
        completion_gate=gate,  # type: ignore[arg-type]
    )
    await service.initialize()
    return service, conversation.id


async def _tree(service: TaskService, session_id: str):  # type: ignore[no-untyped-def]
    return await service.create(
        session_id,
        TaskCreateRequest.model_validate(
            {
                "tasks": [
                    {"client_ref": "phase", "title": "Implement authentication"},
                    {"client_ref": "task", "parent_ref": "phase", "title": "OAuth endpoint"},
                    {"client_ref": "sub1", "parent_ref": "task", "title": "Implement callback"},
                    {"client_ref": "sub2", "parent_ref": "task", "title": "Add tests"},
                ]
            }
        ),
        user_id="anonymous",
        tenant_id="default",
        tool_execution_id="call-plan",
    )


async def _started_task(service: TaskService, session_id: str, *, tool_execution_id: str):  # type: ignore[no-untyped-def]
    created = await service.create(
        session_id,
        TaskCreateRequest(title="Run verification"),
        user_id="anonymous",
        tenant_id="default",
        tool_execution_id=tool_execution_id,
    )
    task = created.snapshot.tasks[0]
    started = await service.update(
        session_id,
        TaskUpdateRequest(task_id=task.task_id, expected_version=task.version, status="in_progress"),
        user_id="anonymous",
        tenant_id="default",
    )
    return started.snapshot.tasks[0]


def test_mysql_task_tables_accept_prefixed_conversation_ids() -> None:
    assert session_tasks_table.c.session_id.type.length == 64
    assert session_task_meta_table.c.session_id.type.length == 64


@pytest.mark.asyncio
async def test_task_tree_autogate_parent_aggregation_and_projection() -> None:
    service, session_id = await _service()
    created = await _tree(service, session_id)
    phase = created.snapshot.tasks[0]
    task = phase.children[0]
    sub1, sub2 = task.children

    started = await service.update(
        session_id,
        TaskUpdateRequest(task_id=sub1.task_id, expected_version=sub1.version, status="in_progress"),
        user_id="anonymous",
        tenant_id="default",
    )
    updated_phase = started.snapshot.tasks[0]
    updated_sub1 = updated_phase.children[0].children[0]
    assert updated_phase.status == "in_progress"
    assert updated_phase.children[0].status == "in_progress"
    assert updated_sub1.status == "in_progress"

    completed = await service.update(
        session_id,
        TaskUpdateRequest(
            task_id=updated_sub1.task_id,
            expected_version=updated_sub1.version,
            status="verifying",
            reason="Callback implemented",
            evidence=[{"tool_call_id": "call-test"}],
        ),
        user_id="anonymous",
        tenant_id="default",
    )
    completed_task = completed.snapshot.tasks[0].children[0]
    completed_sub1, pending_sub2 = completed_task.children
    assert completed_sub1.status == "completed"
    assert completed_sub1.verification_status == "passed"
    assert completed_task.status == "pending"
    assert pending_sub2.task_id == sub2.task_id
    assert completed.snapshot.revision == 4

    projection = await service.context_projection(
        session_id,
        user_id="anonymous",
        tenant_id="default",
    )
    assert "<current_tasks>" in projection
    assert "Progress: 1/2" in projection
    assert f"Next: [{sub2.task_id}] Add tests" in projection


@pytest.mark.asyncio
async def test_task_create_is_transport_idempotent_and_enforces_single_active_leaf() -> None:
    service, session_id = await _service()
    first = await _tree(service, session_id)
    retry = await _tree(service, session_id)
    assert retry.affected_task_ids == first.affected_task_ids
    assert retry.snapshot.revision == first.snapshot.revision

    sub1, sub2 = first.snapshot.tasks[0].children[0].children
    await service.update(
        session_id,
        TaskUpdateRequest(task_id=sub1.task_id, expected_version=1, status="in_progress"),
        user_id="anonymous",
        tenant_id="default",
    )
    with pytest.raises(PlatformError) as exc_info:
        await service.update(
            session_id,
            TaskUpdateRequest(task_id=sub2.task_id, expected_version=1, status="in_progress"),
            user_id="anonymous",
            tenant_id="default",
        )
    assert exc_info.value.code == "ACTIVE_LEAF_CONFLICT"


class RejectingGate:
    async def verify(self, task, evidence):  # type: ignore[no-untyped-def]
        return GateResult(status="failed", reason="Required test evidence is missing")


@pytest.mark.asyncio
async def test_gate_failure_returns_leaf_to_in_progress() -> None:
    service, session_id = await _service(gate=RejectingGate())
    created = await service.create(
        session_id,
        TaskCreateRequest(title="Run tests"),
        user_id="anonymous",
        tenant_id="default",
        tool_execution_id="call-create",
    )
    task = created.snapshot.tasks[0]
    started = await service.update(
        session_id,
        TaskUpdateRequest(task_id=task.task_id, expected_version=task.version, status="in_progress"),
        user_id="anonymous",
        tenant_id="default",
    )
    current = started.snapshot.tasks[0]
    rejected = await service.update(
        session_id,
        TaskUpdateRequest(task_id=current.task_id, expected_version=current.version, status="verifying"),
        user_id="anonymous",
        tenant_id="default",
    )
    task = rejected.snapshot.tasks[0]
    assert task.status == "in_progress"
    assert task.verification_status == "failed"
    assert task.verification_reason == "Required test evidence is missing"


@pytest.mark.asyncio
async def test_autogate_requires_completion_evidence() -> None:
    service, session_id = await _service()
    task = await _started_task(service, session_id, tool_execution_id="call-no-evidence")

    rejected = await service.update(
        session_id,
        TaskUpdateRequest(task_id=task.task_id, expected_version=task.version, status="verifying"),
        user_id="anonymous",
        tenant_id="default",
    )

    task = rejected.snapshot.tasks[0]
    assert task.status == "in_progress"
    assert task.verification_status == "failed"
    assert task.verification_reason == "Completion evidence is required before a task can be completed."


class WaitingGate:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def verify(self, task, evidence):  # type: ignore[no-untyped-def]
        self.started.set()
        await self.release.wait()
        return GateResult(status="passed", reason="Delayed verification passed")


@pytest.mark.asyncio
async def test_verifying_task_rejects_concurrent_metadata_edits() -> None:
    gate = WaitingGate()
    service, session_id = await _service(gate=gate)
    task = await _started_task(service, session_id, tool_execution_id="call-concurrent")
    verifying_call = asyncio.create_task(
        service.update(
            session_id,
            TaskUpdateRequest(
                task_id=task.task_id,
                expected_version=task.version,
                status="verifying",
                evidence=[{"check": "unit-test"}],
            ),
            user_id="anonymous",
            tenant_id="default",
        )
    )
    await gate.started.wait()
    verifying = (await service.query(session_id, user_id="anonymous", tenant_id="default")).tasks[0]

    with pytest.raises(PlatformError) as exc_info:
        await service.update(
            session_id,
            TaskUpdateRequest(
                task_id=verifying.task_id,
                expected_version=verifying.version,
                reason="Concurrent edit",
            ),
            user_id="anonymous",
            tenant_id="default",
        )
    assert exc_info.value.code == "TASK_VERIFICATION_IN_PROGRESS"

    gate.release.set()
    completed = await verifying_call
    assert completed.snapshot.tasks[0].status == "completed"


@pytest.mark.asyncio
async def test_cancelled_gate_returns_task_to_in_progress() -> None:
    gate = WaitingGate()
    service, session_id = await _service(gate=gate)
    task = await _started_task(service, session_id, tool_execution_id="call-cancelled")
    verifying_call = asyncio.create_task(
        service.update(
            session_id,
            TaskUpdateRequest(
                task_id=task.task_id,
                expected_version=task.version,
                status="verifying",
                evidence=[{"check": "unit-test"}],
            ),
            user_id="anonymous",
            tenant_id="default",
        )
    )
    await gate.started.wait()

    verifying_call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await verifying_call

    recovered = (await service.query(session_id, user_id="anonymous", tenant_id="default")).tasks[0]
    assert recovered.status == "in_progress"
    assert recovered.verification_status == "error"
    assert "interrupted" in recovered.verification_reason


@pytest.mark.asyncio
async def test_gate_timeout_returns_task_to_in_progress() -> None:
    gate = WaitingGate()
    service, session_id = await _service(gate=gate)
    service.verification_timeout_seconds = 0.01
    task = await _started_task(service, session_id, tool_execution_id="call-timeout")

    timed_out = await service.update(
        session_id,
        TaskUpdateRequest(
            task_id=task.task_id,
            expected_version=task.version,
            status="verifying",
            evidence=[{"check": "unit-test"}],
        ),
        user_id="anonymous",
        tenant_id="default",
    )

    recovered = timed_out.snapshot.tasks[0]
    assert recovered.status == "in_progress"
    assert recovered.verification_status == "error"
    assert "timed out" in recovered.verification_reason


@pytest.mark.asyncio
async def test_verifying_task_can_be_cancelled_explicitly() -> None:
    gate = WaitingGate()
    service, session_id = await _service(gate=gate)
    task = await _started_task(service, session_id, tool_execution_id="call-manual-cancel")
    verifying_call = asyncio.create_task(
        service.update(
            session_id,
            TaskUpdateRequest(
                task_id=task.task_id,
                expected_version=task.version,
                status="verifying",
                evidence=[{"check": "unit-test"}],
            ),
            user_id="anonymous",
            tenant_id="default",
        )
    )
    await gate.started.wait()
    verifying = (await service.query(session_id, user_id="anonymous", tenant_id="default")).tasks[0]

    cancelled = await service.update(
        session_id,
        TaskUpdateRequest(
            task_id=verifying.task_id,
            expected_version=verifying.version,
            status="in_progress",
            reason="Cancelled by user",
        ),
        user_id="anonymous",
        tenant_id="default",
    )
    gate.release.set()
    original_result = await verifying_call

    assert cancelled.snapshot.tasks[0].status == "in_progress"
    assert cancelled.snapshot.tasks[0].verification_status == "error"
    assert cancelled.snapshot.tasks[0].verification_reason == "Cancelled by user"
    assert original_result.snapshot.tasks[0].status == "in_progress"


@pytest.mark.asyncio
async def test_query_recovers_expired_verification_lease() -> None:
    service, session_id = await _service()
    service.verification_timeout_seconds = 1
    await _started_task(service, session_id, tool_execution_id="call-stale")

    def mark_stale(tasks):  # type: ignore[no-untyped-def]
        current = tasks[0]
        current.status = "verifying"
        current.verification_status = "pending"
        current.updated_at = utc_now() - timedelta(seconds=2)
        current.version += 1
        return MutationDecision(None)

    await service.store.mutate(session_id, "anonymous", mark_stale)
    recovered = (await service.query(session_id, user_id="anonymous", tenant_id="default")).tasks[0]

    assert recovered.status == "in_progress"
    assert recovered.verification_status == "error"
    assert "lease expired" in recovered.verification_reason


@pytest.mark.asyncio
async def test_delete_only_allows_pending_subtrees() -> None:
    service, session_id = await _service()
    created = await _tree(service, session_id)
    task = created.snapshot.tasks[0].children[0]
    deleted = await service.delete(
        session_id,
        TaskDeleteRequest(task_ids=[task.task_id]),
        user_id="anonymous",
        tenant_id="default",
    )
    assert len(deleted.affected_task_ids) == 3
    assert deleted.snapshot.tasks[0].children == []

    root = deleted.snapshot.tasks[0]
    started = await service.update(
        session_id,
        TaskUpdateRequest(task_id=root.task_id, expected_version=root.version, status="in_progress"),
        user_id="anonymous",
        tenant_id="default",
    )
    with pytest.raises(PlatformError) as exc_info:
        await service.delete(
            session_id,
            TaskDeleteRequest(task_ids=[started.snapshot.tasks[0].task_id]),
            user_id="anonymous",
            tenant_id="default",
        )
    assert exc_info.value.code == "TASK_DELETE_NOT_ALLOWED"


class SharedTaskEventRedis:
    def __init__(self) -> None:
        self.subscribers: dict[str, set[asyncio.Queue[int]]] = defaultdict(set)

    async def publish(self, namespace: str, key: str, value: int) -> int:
        channel = f"{namespace}:{key}"
        subscribers = list(self.subscribers.get(channel, set()))
        for queue in subscribers:
            queue.put_nowait(value)
        return len(subscribers)

    @asynccontextmanager
    async def subscribe(self, namespace: str, key: str) -> AsyncIterator[AsyncIterator[int]]:
        channel = f"{namespace}:{key}"
        queue: asyncio.Queue[int] = asyncio.Queue()
        self.subscribers[channel].add(queue)

        async def messages() -> AsyncIterator[int]:
            while True:
                yield await queue.get()

        try:
            yield messages()
        finally:
            self.subscribers[channel].discard(queue)


@pytest.mark.asyncio
async def test_task_events_cross_backend_nodes_through_redis() -> None:
    redis = SharedTaskEventRedis()
    node_a = TaskEventBroker(redis)  # type: ignore[arg-type]
    node_b = TaskEventBroker(redis)  # type: ignore[arg-type]

    async with node_b.subscribe("session") as queue:
        await node_a.publish("session", 8)
        assert await asyncio.wait_for(queue.get(), timeout=1) == 8


def test_parent_status_truth_table() -> None:
    async def scenario() -> None:
        service, session_id = await _service()
        created = await _tree(service, session_id)
        children = created.snapshot.tasks[0].children[0].children
        assert derive_parent_status(children) == "pending"

    asyncio.run(scenario())


def test_agent_task_tool_refreshes_projection_and_http_snapshot() -> None:
    llm = ScriptedLLM(
        [
            call_first_tool(
                description_contains="Create one task or a batch",
                arguments='{"title":"Deliver OAuth support"}',
                call_id="call-task-create",
            ),
            assistant("I created the delivery task."),
        ]
    )
    settings = AgentSettings(_env_file=None, max_agent_iterations=4)  # type: ignore[call-arg]
    with TestClient(create_app(settings=settings, llm=llm)) as client:
        response = client.post("/chat", json={"message": "Plan this work"})
        session_id = response.json()["conversation_id"]
        snapshot = client.get("/tasks", params={"session_id": session_id})

    assert response.status_code == 200
    assert snapshot.status_code == 200
    assert snapshot.json()["tasks"][0]["title"] == "Deliver OAuth support"
    assert "<current_tasks>" in llm.calls[1]["messages"][0]["content"]
    assert "Deliver OAuth support" in llm.calls[1]["messages"][0]["content"]
