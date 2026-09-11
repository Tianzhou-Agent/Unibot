from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, AsyncIterator

import pytest

from tianzhou_agent_platform.core.chat import LLMCallRecord, TraceRecord, TraceSpan
from tianzhou_agent_platform.core.conversation import ConversationCreate
from tianzhou_agent_platform.core.repository import LLM_CALLS_RESOURCE, TRACES_RESOURCE
from tianzhou_agent_platform.core.repository import CONVERSATIONS_RESOURCE
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.models import (
    DeleteResult,
    CacheEntry,
    StorePage,
    StoreQuery,
    StoreRecord,
    WriteResult,
)
from tianzhou_agent_platform.store.repository import PersistentRepository


class FakeMySqlStore:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, StoreRecord]] = {}
        self.queries: list[str] = []

    async def create_tables(self, metadata: Any) -> None:
        del metadata

    async def read(self, resource: str, record_id: str) -> StoreRecord | None:
        return self.records.get(resource, {}).get(record_id)

    async def create(self, resource: str, values: dict[str, Any]) -> StoreRecord:
        record_id = str(values["id"])
        record = StoreRecord(
            resource=resource,
            id=record_id,
            values={key: value for key, value in values.items() if key != "id"},
        )
        self.records.setdefault(resource, {})[record_id] = record
        return record

    async def update(
        self,
        resource: str,
        record_id: str,
        values: dict[str, Any],
    ) -> StoreRecord:
        current = self.records[resource][record_id]
        updated = StoreRecord(
            resource=resource,
            id=record_id,
            values={**current.values, **values},
        )
        self.records[resource][record_id] = updated
        return updated

    async def delete(self, resource: str, record_id: str) -> DeleteResult:
        removed = self.records.get(resource, {}).pop(record_id, None)
        return DeleteResult(deleted=removed is not None)

    async def query(self, resource: str, query: StoreQuery) -> StorePage:
        self.queries.append(resource)
        records = list(self.records.get(resource, {}).values())
        page = records[query.offset : query.offset + query.limit]
        return StorePage(items=page, limit=query.limit, offset=query.offset)


class FakeRedisStore:
    def __init__(self) -> None:
        self.cached_namespaces: list[str] = []
        self.run_locks: set[str] = set()
        self.values: dict[str, Any] = {}
        self.refreshes = 0
        self.leases: dict[str, asyncio.Lock] = {}

    async def set(self, namespace: str, key: str, value: Any) -> WriteResult:
        del key, value
        self.cached_namespaces.append(namespace)
        return WriteResult(written=True)

    async def set_if_absent(
        self,
        namespace: str,
        key: str,
        value: Any,
        *,
        ttl_seconds: int,
    ) -> WriteResult:
        del ttl_seconds
        lock_key = f"{namespace}:{key}"
        if lock_key in self.run_locks:
            return WriteResult(written=False)
        self.run_locks.add(lock_key)
        self.values[lock_key] = value
        return WriteResult(written=True)

    async def get(self, namespace: str, key: str) -> CacheEntry | None:
        lock_key = f"{namespace}:{key}"
        if lock_key not in self.run_locks:
            return None
        return CacheEntry(namespace=namespace, key=key, value=self.values[lock_key])

    async def refresh_if_value(self, namespace: str, key: str, value: Any, *, ttl_seconds: int) -> WriteResult:
        entry = await self.get(namespace, key)
        matched = entry is not None and entry.value == value
        if matched:
            self.refreshes += 1
        return WriteResult(written=matched)

    async def delete_if_value(self, namespace: str, key: str, value: Any) -> DeleteResult:
        entry = await self.get(namespace, key)
        if entry is None or entry.value != value:
            return DeleteResult(deleted=False)
        return await self.delete(namespace, key)

    async def delete(self, namespace: str, key: str) -> DeleteResult:
        lock_key = f"{namespace}:{key}"
        removed = lock_key in self.run_locks
        self.run_locks.discard(lock_key)
        return DeleteResult(deleted=removed)

    @asynccontextmanager
    async def lease(
        self,
        namespace: str,
        key: str,
        *,
        ttl_seconds: int,
        blocking_timeout_seconds: float | None = None,
    ) -> AsyncIterator[bool]:
        lock = self.leases.setdefault(f"{namespace}:{key}", asyncio.Lock())
        if lock.locked() and blocking_timeout_seconds is None:
            yield False
            return
        async with asyncio.timeout(blocking_timeout_seconds):
            await lock.acquire()
        try:
            yield True
        finally:
            lock.release()


def _stores() -> StorageStores:
    return StorageStores(
        mysql=FakeMySqlStore(),  # type: ignore[arg-type]
        redis=FakeRedisStore(),  # type: ignore[arg-type]
        nas=None,  # type: ignore[arg-type]
    )


def _trace(trace_id: str = "trace_legacy") -> TraceRecord:
    now = datetime.now(UTC)
    return TraceRecord(
        trace_id=trace_id,
        root_span_id="span_root",
        conversation_id="conv_legacy",
        user_id="user_1",
        tenant_id="tenant_1",
        spans=[
            TraceSpan(
                span_id="span_root",
                kind="agent",
                name="agent.run",
                status="completed",
                started_at=now,
                completed_at=now,
            )
        ],
        status="completed",
        created_at=now,
        completed_at=now,
    )


def _call() -> LLMCallRecord:
    return LLMCallRecord(
        call_id="llm_legacy",
        trace_id="trace_legacy",
        span_id="span_model",
        endpoint="https://model.invalid/v1",
        model="test-model",
        request={"model": "test-model"},
        status="completed",
        created_at=datetime.now(UTC),
    )


def _seed(stores: StorageStores, resource: str, record_id: str, value: Any) -> None:
    mysql = stores.mysql
    mysql.records.setdefault(resource, {})[record_id] = StoreRecord(  # type: ignore[attr-defined]
        resource=resource,
        id=record_id,
        values={
            "payload": value.model_dump(mode="json"),
            "updated_at": datetime.now(UTC),
        },
    )


@pytest.mark.asyncio
async def test_legacy_observability_is_loaded_only_when_old_api_is_used() -> None:
    stores = _stores()
    _seed(stores, TRACES_RESOURCE, "trace_legacy", _trace())
    _seed(stores, LLM_CALLS_RESOURCE, "llm_legacy", _call())
    repository = PersistentRepository(stores)

    await repository.initialize()

    mysql = stores.mysql
    assert TRACES_RESOURCE not in mysql.queries  # type: ignore[attr-defined]
    assert LLM_CALLS_RESOURCE not in mysql.queries  # type: ignore[attr-defined]
    assert repository._traces == {}  # noqa: SLF001
    assert repository._llm_calls == {}  # noqa: SLF001

    assert [trace.trace_id for trace in await repository.list_traces()] == [
        "trace_legacy"
    ]
    assert [call.call_id for call in await repository.list_llm_calls()] == [
        "llm_legacy"
    ]
    redis = stores.redis
    assert f"repository:{TRACES_RESOURCE}" not in redis.cached_namespaces  # type: ignore[attr-defined]
    assert f"repository:{LLM_CALLS_RESOURCE}" not in redis.cached_namespaces  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_rollback_switch_persists_and_recovers_observability() -> None:
    stores = _stores()
    repository = PersistentRepository(stores, persist_observability=True)
    await repository.initialize()
    await repository.create_trace(_trace())
    await repository.upsert_llm_call(_call())

    restarted = PersistentRepository(stores, persist_observability=True)
    await restarted.initialize()

    assert (await restarted.get_trace("trace_legacy")).trace_id == "trace_legacy"
    assert [call.call_id for call in await restarted.list_llm_calls()] == [
        "llm_legacy"
    ]


@pytest.mark.asyncio
async def test_recent_obs_miss_keeps_running_state_and_run_lock() -> None:
    stores = _stores()

    async def not_visible_yet(trace_id: str) -> str | None:
        del trace_id
        return None

    repository = PersistentRepository(
        stores,
        obs_trace_status_resolver=not_visible_yet,
    )
    await repository.initialize()
    conversation = await repository.create_conversation(
        ConversationCreate(user_id="user_1", tenant_id="tenant_1", title="test")
    )
    await repository.start_conversation_run(conversation.id, "trace_not_visible")

    reconciled = await repository.reconcile_conversation_run(conversation.id)

    assert reconciled.run_status == "running"
    assert reconciled.active_trace_id == "trace_not_visible"
    redis = stores.redis
    assert f"conversation-run:{conversation.id}" in redis.run_locks  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_expired_run_recovers_even_when_obs_still_says_running() -> None:
    stores = _stores()

    async def stale_status(trace_id: str) -> str:
        return "running"

    original = PersistentRepository(stores, obs_trace_status_resolver=stale_status)
    await original.initialize()
    conversation = await original.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))
    # Seed the durable state left by a dead worker, without creating a live task.
    stale = conversation.model_copy(update={
        "run_status": "running",
        "active_trace_id": "trace_dead",
        "run_started_at": datetime.now(UTC) - timedelta(days=1),
    })
    await original._save_record(CONVERSATIONS_RESOURCE, conversation.id, stale)
    restarted = PersistentRepository(stores, obs_trace_status_resolver=stale_status)
    await restarted.initialize()

    recovered = await restarted.reconcile_conversation_run(conversation.id)
    assert recovered.run_status == "failed"
    assert recovered.active_trace_id is None
    assert recovered.run_error
    await restarted.start_conversation_run(conversation.id, "trace_new")
    await restarted.finish_conversation_run(conversation.id)


@pytest.mark.asyncio
async def test_active_run_lease_renews_and_is_released_on_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tianzhou_agent_platform.store.repository.CONVERSATION_RUN_HEARTBEAT_SECONDS", 0.01)
    stores = _stores()
    repository = PersistentRepository(stores)
    await repository.initialize()
    conversation = await repository.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))
    await repository.start_conversation_run(conversation.id, "trace_live")
    try:
        async with asyncio.timeout(1):
            while stores.redis.refreshes < 2:
                await asyncio.sleep(0.01)
        assert (await repository.reconcile_conversation_run(conversation.id)).run_status == "running"
    finally:
        await repository.finish_conversation_run(conversation.id)
    assert await stores.redis.get("conversation-run", conversation.id) is None
    assert not repository._conversation_run_tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("same_repository", [False, True])
async def test_old_run_cannot_finish_or_release_a_replacement_run(same_repository) -> None:
    stores = _stores()
    first = PersistentRepository(stores)
    second = first if same_repository else PersistentRepository(stores)
    await first.initialize()
    conversation = await first.create_conversation(ConversationCreate(user_id="u1", tenant_id="t1"))
    await first.start_conversation_run(conversation.id, "trace_old")
    stores.redis.run_locks.clear()
    await second.initialize()
    await second.start_conversation_run(conversation.id, "trace_new")
    try:
        await first.finish_conversation_run(conversation.id, expected_trace_id="trace_old")
        current = await second.get_conversation(conversation.id)
        assert current.run_status == "running"
        assert current.active_trace_id == "trace_new"
        entry = await stores.redis.get("conversation-run", conversation.id)
        assert entry is not None and entry.value == {"trace_id": "trace_new"}
    finally:
        await second.finish_conversation_run(conversation.id)


@pytest.mark.asyncio
async def test_live_lease_still_reconciles_a_conclusively_completed_obs_trace():
    stores = _stores()

    async def completed(trace_id):
        return "completed"

    repository = PersistentRepository(stores, obs_trace_status_resolver=completed)
    await repository.initialize()
    conversation = await repository.create_conversation(ConversationCreate())
    await repository.start_conversation_run(conversation.id, "trace_done")
    result = await repository.reconcile_conversation_run(conversation.id)
    assert result.run_status == "idle"
    assert await stores.redis.get("conversation-run", conversation.id) is None
    assert not repository._conversation_run_tasks


@pytest.mark.asyncio
async def test_finishing_a_run_waits_for_concurrent_reconciliation():
    stores = _stores()
    repository = PersistentRepository(stores)
    await repository.initialize()
    conversation = await repository.create_conversation(ConversationCreate())
    await repository.start_conversation_run(conversation.id, "trace_wait")
    async with stores.redis.lease("conversation-run-state", conversation.id, ttl_seconds=30):
        finish = asyncio.create_task(repository.finish_conversation_run(
            conversation.id, expected_trace_id="trace_wait",
        ))
        await asyncio.sleep(0.02)
        assert not finish.done()
    assert (await finish).run_status == "idle"
    assert not repository._conversation_run_tasks


@pytest.mark.asyncio
async def test_lost_lease_cancels_its_worker_and_releases_local_heartbeat(monkeypatch):
    monkeypatch.setattr("tianzhou_agent_platform.store.repository.CONVERSATION_RUN_HEARTBEAT_SECONDS", 0.01)
    stores = _stores()
    repository = PersistentRepository(stores)
    await repository.initialize()
    conversation = await repository.create_conversation(ConversationCreate())
    started = asyncio.Event()

    async def worker():
        await repository.start_conversation_run(conversation.id, "trace_lost")
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            await repository.finish_conversation_run(
                conversation.id, status="failed", expected_trace_id="trace_lost",
            )

    task = asyncio.create_task(worker())
    await started.wait()
    stores.redis.run_locks.clear()
    with pytest.raises(asyncio.CancelledError):
        async with asyncio.timeout(1):
            await task
    assert (await repository.get_conversation(conversation.id)).run_status == "failed"
    assert not repository._conversation_run_tasks
