from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import uuid4

from tianzhou_agent_platform.core.base import utc_now
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.store.errors import StorageError
from tianzhou_agent_platform.store.redis.client import RedisStore
from tianzhou_agent_platform.tasks.models import (
    GateResult,
    SessionTask,
    TaskCreateItem,
    TaskCreateRequest,
    TaskDeleteRequest,
    TaskMutationResponse,
    TaskNode,
    TaskStatus,
    TaskTreeSnapshot,
    TaskUpdateRequest,
)
from tianzhou_agent_platform.tasks.store import MutationDecision, SessionTaskStore

ACTIVE_LEAF_STATUSES = {"in_progress", "verifying"}
FINAL_STATUSES = {"completed", "skipped"}
MAX_TASKS_PER_SESSION = 100


class CompletionGate(Protocol):
    async def verify(self, task: SessionTask, evidence: list[dict[str, Any]]) -> GateResult: ...


class AutoCompletionGate:
    async def verify(self, task: SessionTask, evidence: list[dict[str, Any]]) -> GateResult:
        if not any(item for item in evidence):
            return GateResult(
                status="failed",
                reason="Completion evidence is required before a task can be completed.",
            )
        return GateResult(status="passed", reason="V1 AutoGate accepted the completion request.")


class TaskEventBroker:
    def __init__(self, redis: RedisStore | None = None) -> None:
        self._redis = redis
        self._subscribers: dict[str, set[asyncio.Queue[int]]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def publish(self, session_id: str, revision: int) -> None:
        async with self._lock:
            queues = list(self._subscribers.get(session_id, set()))
        for queue in queues:
            self._offer(queue, revision)
        if self._redis is not None:
            try:
                await self._redis.publish("task:events", session_id, revision)
            except StorageError:
                pass

    @asynccontextmanager
    async def subscribe(self, session_id: str) -> AsyncIterator[asyncio.Queue[int]]:
        queue: asyncio.Queue[int] = asyncio.Queue(maxsize=1)
        async with self._lock:
            self._subscribers[session_id].add(queue)
        redis_context: Any | None = None
        relay_task: asyncio.Task[None] | None = None
        if self._redis is not None:
            try:
                redis_context = self._redis.subscribe("task:events", session_id)
                messages = await redis_context.__aenter__()

                async def relay() -> None:
                    try:
                        async for value in messages:
                            self._offer(queue, int(value))
                    except (StorageError, TypeError, ValueError):
                        return

                relay_task = asyncio.create_task(relay())
            except StorageError:
                redis_context = None
        try:
            yield queue
        finally:
            if relay_task is not None:
                relay_task.cancel()
                with suppress(asyncio.CancelledError):
                    await relay_task
            if redis_context is not None:
                await redis_context.__aexit__(None, None, None)
            async with self._lock:
                subscribers = self._subscribers.get(session_id)
                if subscribers is not None:
                    subscribers.discard(queue)
                    if not subscribers:
                        self._subscribers.pop(session_id, None)

    @staticmethod
    def _offer(queue: asyncio.Queue[int], revision: int) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        queue.put_nowait(revision)


class TaskService:
    def __init__(
        self,
        repository: InMemoryRepository,
        store: SessionTaskStore,
        *,
        completion_gate: CompletionGate | None = None,
        event_broker: TaskEventBroker | None = None,
        verification_timeout_seconds: float = 60.0,
    ) -> None:
        if verification_timeout_seconds <= 0:
            raise ValueError("verification_timeout_seconds must be greater than zero")
        self.repository = repository
        self.store = store
        self.completion_gate = completion_gate or AutoCompletionGate()
        self.events = event_broker or TaskEventBroker()
        self.verification_timeout_seconds = verification_timeout_seconds

    async def initialize(self) -> None:
        await self.store.initialize()

    async def query(
        self,
        session_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> TaskTreeSnapshot:
        await self._authorize(session_id, user_id=user_id, tenant_id=tenant_id)
        tasks, revision = await self.store.read(session_id)
        self._validate_task_owners(tasks, user_id)
        if any(_verification_is_stale(task, self.verification_timeout_seconds) for task in tasks):
            now = utc_now()

            def recover(tasks_to_recover: list[SessionTask]) -> MutationDecision[None]:
                changed = False
                for task in tasks_to_recover:
                    if not _verification_is_stale(task, self.verification_timeout_seconds, now=now):
                        continue
                    task.status = "in_progress"
                    task.verification_status = "error"
                    task.verification_reason = "Verification lease expired; returned to in_progress for retry."
                    task.verified_at = now
                    task.version += 1
                    task.updated_at = now
                    changed = True
                if changed:
                    _derive_all_parent_statuses(tasks_to_recover, now=now)
                return MutationDecision(None, changed=changed)

            recovered = await self._mutate(session_id, user_id, recover)
            tasks, revision = recovered.tasks, recovered.revision
        return _snapshot(session_id, tasks, revision)

    async def create(
        self,
        session_id: str,
        request: TaskCreateRequest,
        *,
        user_id: str,
        tenant_id: str,
        tool_execution_id: str,
    ) -> TaskMutationResponse:
        await self._authorize(session_id, user_id=user_id, tenant_id=tenant_id)
        items = request.items()

        def mutation(tasks: list[SessionTask]) -> MutationDecision[list[str]]:
            if len(items) > 20:
                raise _task_error("TASK_LIMIT_EXCEEDED", "A task_create batch can contain at most 20 tasks")
            refs = [item.client_ref for item in items if item.client_ref]
            if len(refs) != len(set(refs)):
                raise _task_error("INVALID_REQUEST", "client_ref values must be unique within a batch")

            by_id = {task.task_id: task for task in tasks}
            by_idempotency = {task.idempotency_key: task for task in tasks if task.idempotency_key}
            resolved_refs: dict[str, str] = {}
            item_ids: list[str] = []
            new_items: list[tuple[int, TaskCreateItem, str, str]] = []
            for index, item in enumerate(items):
                item_ref = item.client_ref or f"item_{index + 1}"
                idempotency_key = f"{tool_execution_id}:{item_ref}"
                existing = by_idempotency.get(idempotency_key)
                if existing is not None:
                    task_id = existing.task_id
                else:
                    task_id = str(uuid4())
                    new_items.append((index, item, task_id, idempotency_key))
                item_ids.append(task_id)
                if item.client_ref:
                    resolved_refs[item.client_ref] = task_id

            if len(tasks) + len(new_items) > MAX_TASKS_PER_SESSION:
                raise _task_error(
                    "TASK_LIMIT_EXCEEDED",
                    f"A session can contain at most {MAX_TASKS_PER_SESSION} tasks",
                )

            proposed_parents: dict[str, str | None] = {}
            item_by_id: dict[str, TaskCreateItem] = {}
            for _index, item, task_id, _key in new_items:
                if item.parent_ref:
                    parent_id = resolved_refs.get(item.parent_ref)
                    if parent_id is None:
                        raise _task_error("TASK_NOT_FOUND", f"Unknown parent_ref {item.parent_ref!r}")
                else:
                    parent_id = item.parent_task_id
                if parent_id is not None and parent_id not in by_id and parent_id not in item_ids:
                    raise _task_error("TASK_NOT_FOUND", "Parent task does not belong to the current session", 404)
                if parent_id == task_id:
                    raise _task_error("TASK_PARENT_CYCLE", "A task cannot be its own parent")
                proposed_parents[task_id] = parent_id
                item_by_id[task_id] = item

            depth_cache: dict[str, int] = {task.task_id: task.depth for task in tasks}

            def depth_for(task_id: str, visiting: set[str] | None = None) -> int:
                if task_id in depth_cache:
                    return depth_cache[task_id]
                visiting = set(visiting or set())
                if task_id in visiting:
                    raise _task_error("TASK_PARENT_CYCLE", "Task parent references form a cycle")
                visiting.add(task_id)
                parent_id = proposed_parents.get(task_id)
                depth = 0 if parent_id is None else depth_for(parent_id, visiting) + 1
                if depth > 2:
                    raise _task_error("TASK_DEPTH_EXCEEDED", "V1 task trees are limited to three levels")
                depth_cache[task_id] = depth
                return depth

            sibling_order: dict[str | None, int] = defaultdict(int)
            for task in tasks:
                sibling_order[task.parent_task_id] = max(sibling_order[task.parent_task_id], task.sort_order + 1)
            now = utc_now()
            for _index, item, task_id, idempotency_key in new_items:
                parent_id = proposed_parents[task_id]
                task = SessionTask(
                    task_id=task_id,
                    session_id=session_id,
                    owner_user_id=user_id,
                    title=item.title,
                    description=item.description,
                    parent_task_id=parent_id,
                    depth=depth_for(task_id),
                    sort_order=sibling_order[parent_id],
                    idempotency_key=idempotency_key,
                    created_at=now,
                    updated_at=now,
                )
                sibling_order[parent_id] += 1
                tasks.append(task)
            _derive_all_parent_statuses(tasks, now=now)
            return MutationDecision(item_ids, changed=bool(new_items))

        commit = await self._mutate(session_id, user_id, mutation)
        return TaskMutationResponse(
            affected_task_ids=commit.value,
            snapshot=_snapshot(session_id, commit.tasks, commit.revision),
        )

    async def update(
        self,
        session_id: str,
        request: TaskUpdateRequest,
        *,
        user_id: str,
        tenant_id: str,
    ) -> TaskMutationResponse:
        await self._authorize(session_id, user_id=user_id, tenant_id=tenant_id)

        def initial_mutation(tasks: list[SessionTask]) -> MutationDecision[tuple[str, int | None]]:
            by_id = {task.task_id: task for task in tasks}
            task = by_id.get(request.task_id)
            if task is None:
                raise _task_error("TASK_NOT_FOUND", "Task does not belong to the current session", 404)
            if task.version != request.expected_version:
                raise _task_error(
                    "TASK_VERSION_CONFLICT",
                    f"Expected version {request.expected_version}, current version is {task.version}",
                    409,
                    debug={"current_version": task.version},
                )
            changed_fields = request.model_fields_set - {"task_id", "expected_version"}
            if task.status == "verifying" and (
                request.status != "in_progress" or not changed_fields <= {"status", "reason"}
            ):
                raise _task_error(
                    "TASK_VERIFICATION_IN_PROGRESS",
                    "A verifying task only accepts an explicit status=in_progress cancellation",
                    409,
                )
            children = _children_map(tasks)
            has_children = bool(children.get(task.task_id))
            if has_children and request.status is not None:
                raise _task_error(
                    "PARENT_STATUS_SYSTEM_MANAGED",
                    "Parent task status is derived from its children",
                    409,
                )
            if "parent_task_id" in request.model_fields_set:
                if has_children:
                    raise _task_error("INVALID_REQUEST", "V1 only allows reparenting leaf tasks")
                new_parent = request.parent_task_id
                if new_parent == task.task_id:
                    raise _task_error("TASK_PARENT_CYCLE", "A task cannot be its own parent")
                if new_parent is not None:
                    parent = by_id.get(new_parent)
                    if parent is None:
                        raise _task_error("TASK_NOT_FOUND", "Parent task does not belong to this session", 404)
                    if parent.depth + 1 > 2:
                        raise _task_error("TASK_DEPTH_EXCEEDED", "V1 task trees are limited to three levels")
                    task.depth = parent.depth + 1
                else:
                    task.depth = 0
                task.parent_task_id = new_parent
                task.sort_order = max(
                    (item.sort_order for item in tasks if item.parent_task_id == new_parent and item.task_id != task.task_id),
                    default=-1,
                ) + 1

            if request.status is not None:
                _validate_transition(task.status, request.status)
                if request.status in ACTIVE_LEAF_STATUSES:
                    _validate_single_active_leaf(tasks, exclude_task_id=task.task_id)
            if request.title is not None:
                task.title = request.title
            if request.description is not None:
                task.description = request.description
            if request.reason is not None:
                task.reason = request.reason
            if request.evidence is not None:
                task.evidence = request.evidence
            now = utc_now()
            if request.status is not None:
                previous_status = task.status
                task.status = request.status
                if request.status == "verifying":
                    task.verification_status = "pending"
                    task.verification_reason = ""
                    task.verified_at = None
                elif previous_status == "verifying" and request.status == "in_progress":
                    task.verification_status = "error"
                    task.verification_reason = request.reason or "Verification was cancelled and can be retried."
                    task.verified_at = now
                elif request.status != "in_progress":
                    task.verification_status = "none"
                    task.verification_reason = ""
                    task.verified_at = None
            task.version += 1
            task.updated_at = now
            _derive_all_parent_statuses(tasks, now=now)
            started_version = task.version if request.status == "verifying" else None
            return MutationDecision((task.task_id, started_version))

        initial = await self._mutate(session_id, user_id, initial_mutation)
        task_id, started_version = initial.value
        if started_version is None:
            return TaskMutationResponse(
                affected_task_ids=[task_id],
                snapshot=_snapshot(session_id, initial.tasks, initial.revision),
            )

        verifying_task = _task_by_id(initial.tasks, task_id)
        cancelled_error: asyncio.CancelledError | None = None
        try:
            gate_result = await asyncio.wait_for(
                self.completion_gate.verify(verifying_task, verifying_task.evidence),
                timeout=self.verification_timeout_seconds,
            )
        except TimeoutError:
            gate_result = GateResult(
                status="error",
                reason=f"Completion Gate timed out after {self.verification_timeout_seconds:g} seconds.",
            )
        except asyncio.CancelledError as exc:
            cancelled_error = exc
            gate_result = GateResult(
                status="error",
                reason="Completion Gate was interrupted; returned to in_progress for retry.",
            )
        except Exception as exc:
            gate_result = GateResult(status="error", reason=f"Completion Gate error: {exc}")

        def gate_mutation(tasks: list[SessionTask]) -> MutationDecision[str]:
            task = _task_by_id(tasks, task_id)
            if task.status != "verifying" or task.version != started_version:
                return MutationDecision(task_id, changed=False)
            now = utc_now()
            if gate_result.status == "passed":
                task.status = "completed"
                task.verification_status = "passed"
            elif gate_result.status == "failed":
                task.status = "in_progress"
                task.verification_status = "failed"
            else:
                task.status = "in_progress"
                task.verification_status = "error"
            task.verification_reason = gate_result.reason
            task.verified_at = now
            task.version += 1
            task.updated_at = now
            _derive_all_parent_statuses(tasks, now=now)
            return MutationDecision(task_id)

        completed = await asyncio.shield(self._mutate(session_id, user_id, gate_mutation))
        if cancelled_error is not None:
            raise cancelled_error
        return TaskMutationResponse(
            affected_task_ids=[task_id],
            snapshot=_snapshot(session_id, completed.tasks, completed.revision),
        )

    async def delete(
        self,
        session_id: str,
        request: TaskDeleteRequest,
        *,
        user_id: str,
        tenant_id: str,
    ) -> TaskMutationResponse:
        await self._authorize(session_id, user_id=user_id, tenant_id=tenant_id)

        def mutation(tasks: list[SessionTask]) -> MutationDecision[list[str]]:
            by_id = {task.task_id: task for task in tasks}
            missing = [task_id for task_id in request.task_ids if task_id not in by_id]
            if missing:
                raise _task_error("TASK_NOT_FOUND", "One or more tasks do not belong to this session", 404)
            children = _children_map(tasks)
            removed: set[str] = set()

            def collect(task_id: str) -> None:
                if task_id in removed:
                    return
                task = by_id[task_id]
                if task.status != "pending":
                    raise _task_error(
                        "TASK_DELETE_NOT_ALLOWED",
                        "Only pending tasks and pending subtrees can be deleted",
                        409,
                    )
                removed.add(task_id)
                for child in children.get(task_id, []):
                    collect(child.task_id)

            for task_id in request.task_ids:
                collect(task_id)
            tasks[:] = [task for task in tasks if task.task_id not in removed]
            _derive_all_parent_statuses(tasks, now=utc_now())
            return MutationDecision(sorted(removed))

        commit = await self._mutate(session_id, user_id, mutation)
        return TaskMutationResponse(
            affected_task_ids=commit.value,
            snapshot=_snapshot(session_id, commit.tasks, commit.revision),
        )

    async def context_projection(
        self,
        session_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> str:
        snapshot = await self.query(session_id, user_id=user_id, tenant_id=tenant_id)
        flat = _flatten_nodes(snapshot.tasks)
        if not flat:
            return ""
        parent_ids = {task.parent_task_id for task in flat if task.parent_task_id}
        leaves = [task for task in flat if task.task_id not in parent_ids]
        ordered = sorted(leaves, key=lambda item: (item.depth, item.sort_order, item.created_at, item.task_id))
        done = sum(task.status in FINAL_STATUSES for task in leaves)
        current = next((task for task in ordered if task.status in ACTIVE_LEAF_STATUSES), None)
        next_task = next((task for task in ordered if task.status == "pending"), None)
        verified = sorted(
            (task for task in flat if task.verified_at),
            key=lambda item: item.verified_at.timestamp() if item.verified_at else 0.0,
            reverse=True,
        )
        lines = ["<current_tasks>", f"Revision: {snapshot.revision}", f"Progress: {done}/{len(leaves)}"]
        lines.append(_projection_line("Current", current))
        lines.append(_projection_line("Next", next_task))
        if verified:
            last = verified[0]
            lines.append(
                f"Last verification: {last.verification_status} [{last.task_id}]"
                f"{f' - {last.verification_reason}' if last.verification_reason else ''}"
            )
        else:
            lines.append("Last verification: none")
        lines.append("Use task_query for the complete task tree and current versions.")
        lines.append("</current_tasks>")
        return "\n".join(lines)

    async def _authorize(self, session_id: str, *, user_id: str, tenant_id: str) -> None:
        await self.repository.require_conversation_actor(
            session_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    async def _mutate(self, session_id: str, user_id: str, mutator: Any) -> Any:
        try:
            commit = await self.store.mutate(session_id, user_id, mutator)
        except PermissionError as exc:
            raise PlatformError("PERMISSION_DENIED", str(exc), status_code=403) from exc
        if commit.changed:
            await self.events.publish(session_id, commit.revision)
        return commit

    @staticmethod
    def _validate_task_owners(tasks: list[SessionTask], user_id: str) -> None:
        if any(task.owner_user_id != user_id for task in tasks):
            raise PlatformError("PERMISSION_DENIED", "Task tree ownership does not match the caller", status_code=403)


def derive_parent_status(children: list[SessionTask]) -> TaskStatus:
    if children and all(child.status == "skipped" for child in children):
        return "skipped"
    if children and all(child.status in FINAL_STATUSES for child in children):
        return "completed"
    if any(child.status in ACTIVE_LEAF_STATUSES for child in children):
        return "in_progress"
    if any(child.status == "failed" for child in children):
        return "failed"
    return "pending"


def _derive_all_parent_statuses(tasks: list[SessionTask], *, now: datetime) -> None:
    children = _children_map(tasks)
    for task in sorted(tasks, key=lambda item: item.depth, reverse=True):
        direct_children = children.get(task.task_id, [])
        if not direct_children:
            continue
        derived = derive_parent_status(direct_children)
        if task.status != derived:
            task.status = derived
            task.version += 1
            task.updated_at = now


def _children_map(tasks: list[SessionTask]) -> dict[str, list[SessionTask]]:
    children: dict[str, list[SessionTask]] = defaultdict(list)
    for task in tasks:
        if task.parent_task_id:
            children[task.parent_task_id].append(task)
    for values in children.values():
        values.sort(key=lambda item: (item.sort_order, item.created_at, item.task_id))
    return children


def _validate_single_active_leaf(tasks: list[SessionTask], *, exclude_task_id: str) -> None:
    children = _children_map(tasks)
    conflict = next(
        (
            task
            for task in tasks
            if task.task_id != exclude_task_id
            and not children.get(task.task_id)
            and task.status in ACTIVE_LEAF_STATUSES
        ),
        None,
    )
    if conflict is not None:
        raise _task_error(
            "ACTIVE_LEAF_CONFLICT",
            f"Task {conflict.task_id} is already active in this session",
            409,
        )


def _verification_is_stale(
    task: SessionTask,
    timeout_seconds: float,
    *,
    now: datetime | None = None,
) -> bool:
    if task.status != "verifying":
        return False
    checked_at = now or utc_now()
    updated_at = task.updated_at
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return updated_at <= checked_at - timedelta(seconds=timeout_seconds)


def _validate_transition(current: TaskStatus, target: TaskStatus) -> None:
    allowed: dict[TaskStatus, set[TaskStatus]] = {
        "pending": {"in_progress", "skipped"},
        "in_progress": {"pending", "verifying", "skipped", "failed"},
        "verifying": {"in_progress"},
        "completed": set(),
        "skipped": set(),
        "failed": {"pending"},
    }
    if target not in allowed[current]:
        raise _task_error("INVALID_TASK_TRANSITION", f"Cannot transition task from {current} to {target}", 409)


def _snapshot(session_id: str, tasks: list[SessionTask], revision: int) -> TaskTreeSnapshot:
    children = _children_map(tasks)

    def node(task: SessionTask) -> TaskNode:
        return TaskNode(**task.model_dump(), children=[node(child) for child in children.get(task.task_id, [])])

    roots = sorted(
        (task for task in tasks if task.parent_task_id is None),
        key=lambda item: (item.sort_order, item.created_at, item.task_id),
    )
    return TaskTreeSnapshot(session_id=session_id, revision=revision, tasks=[node(task) for task in roots])


def _flatten_nodes(nodes: list[TaskNode]) -> list[TaskNode]:
    result: list[TaskNode] = []
    for node in nodes:
        result.append(node)
        result.extend(_flatten_nodes(node.children))
    return result


def _task_by_id(tasks: list[SessionTask], task_id: str) -> SessionTask:
    task = next((item for item in tasks if item.task_id == task_id), None)
    if task is None:
        raise _task_error("TASK_NOT_FOUND", "Task does not belong to the current session", 404)
    return task


def _projection_line(label: str, task: TaskNode | None) -> str:
    if task is None:
        return f"{label}: none"
    return f"{label}: [{task.task_id}] {task.title} ({task.status}, version={task.version})"


def _task_error(
    code: str,
    message: str,
    status_code: int = 400,
    *,
    debug: dict[str, Any] | None = None,
) -> PlatformError:
    return PlatformError(code, message, status_code=status_code, source="task", debug=debug)
