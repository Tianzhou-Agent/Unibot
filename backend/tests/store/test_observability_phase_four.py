"""Phase-four tests: Trace/LLMCall stop flowing through the generic
repository; startup no longer loads them; conversation run reconciliation
falls back to the OBS pipeline. Uses real MySQL when reachable.
"""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime

import pytest

from tianzhou_agent_platform.core.chat import LLMCallRecord, TraceRecord, TraceSpan
from tianzhou_agent_platform.core.repository import TRACES_RESOURCE
from tianzhou_agent_platform.store import MySqlStore, RedisStore
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.models import StoreQuery
from tianzhou_agent_platform.store.observability_store import OBS_METADATA, ObservabilityStore
from tianzhou_agent_platform.store.repository import PersistentRepository, repository_metadata, repository_tables

MYSQL_DSN = os.getenv("OBS_TEST_MYSQL_DSN", "")
REDIS_URL = os.getenv("OBS_TEST_REDIS_URL", "redis://127.0.0.1:16379/0")


def _can_connect() -> bool:
    if not MYSQL_DSN:
        return False
    try:
        host, port = MYSQL_DSN.split("@")[1].split("/")[0].split(":")
        with socket.create_connection((host, int(port)), timeout=2):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _can_connect(), reason="MySQL is not reachable for phase-four tests")


@pytest.fixture
async def repo() -> PersistentRepository:
    mysql = MySqlStore.from_dsn(MYSQL_DSN, resource_tables=repository_tables)
    async with mysql._engine.begin() as connection:  # noqa: SLF001
        await connection.run_sync(repository_metadata.drop_all)
        await connection.run_sync(repository_metadata.create_all)
    redis = RedisStore.from_url(REDIS_URL, socket_timeout=2.0)
    stores = StorageStores(mysql=mysql, redis=redis, nas=None)  # type: ignore[arg-type]
    persistent = PersistentRepository(stores)
    await persistent.initialize()
    yield persistent
    await stores.close()


def _trace(trace_id: str = "trace_aaa") -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        root_span_id="span_root",
        conversation_id="conv_1",
        user_id="user_1",
        tenant_id="tenant_1",
        spans=[TraceSpan(span_id="span_root", kind="agent", name="agent.run", status="completed")],
        status="completed",
        created_at=datetime.now(UTC),
        completed_at=datetime.now(UTC),
    )


def _call(call_id: str = "llm_1") -> LLMCallRecord:
    return LLMCallRecord(
        call_id=call_id,
        trace_id="trace_aaa",
        span_id="span_aaa",
        endpoint="http://llm",
        model="gpt-test",
        request={"model": "gpt-test"},
        status="completed",
        created_at=datetime.now(UTC),
    )


async def test_trace_not_persisted_to_generic_tables(repo: PersistentRepository) -> None:
    await repo.create_trace(_trace())
    await repo.upsert_llm_call(_call())
    # in-memory copies exist for the current process
    assert await repo.get_trace("trace_aaa") is not None
    assert len(await repo.list_llm_calls()) == 1
    # MySQL generic tables stay empty
    traces = await repo.stores.mysql.query(TRACES_RESOURCE, StoreQuery(limit=100))
    assert traces.items == []
    calls = await repo.stores.mysql.query("llm_calls", StoreQuery(limit=100))
    assert calls.items == []


async def test_restart_does_not_load_traces(repo: PersistentRepository) -> None:
    await repo.create_trace(_trace())
    await repo.upsert_llm_call(_call())
    # simulate a restart: a fresh repository initializes without traces
    fresh = PersistentRepository(repo.stores)
    await fresh.initialize()
    assert len(await fresh.list_traces()) == 0
    assert len(await fresh.list_llm_calls()) == 0
    with pytest.raises(Exception):
        await fresh.get_trace("trace_aaa")


async def test_persist_observability_switch_restores_legacy(repo: PersistentRepository) -> None:
    legacy = PersistentRepository(repo.stores, persist_observability=True)
    await legacy.initialize()
    await legacy.create_trace(_trace())
    traces = await legacy.stores.mysql.query(TRACES_RESOURCE, StoreQuery(limit=100))
    assert len(traces.items) == 1


async def test_reconcile_falls_back_to_obs_resolver(repo: PersistentRepository) -> None:
    from tianzhou_agent_platform.core.conversation import Conversation, ConversationCreate, Message

    async def resolver(trace_id: str) -> str | None:
        return "completed" if trace_id == "trace_aaa" else None

    repo.obs_trace_status_resolver = resolver
    conversation = await repo.create_conversation(
        ConversationCreate(
            user_id="user_1",
            tenant_id="tenant_1",
            title="t",
        )
    )
    started = await repo.start_conversation_run(conversation.id, "trace_aaa")
    assert started.run_status == "running"
    reconciled = await repo.reconcile_conversation_run(conversation.id)
    assert reconciled.run_status == "idle"  # resolved completed -> idle


async def test_obs_resolver_queries_obs_store() -> None:
    obs = ObservabilityStore.from_dsn(MYSQL_DSN)
    async with obs._engine.begin() as connection:  # noqa: SLF001
        await connection.run_sync(OBS_METADATA.drop_all)
        await connection.run_sync(OBS_METADATA.create_all)
    from tianzhou_agent_platform.store.observability_wal import ObsRecord

    await obs.bulk_upsert(
        [
            ObsRecord(
                record_type="trace_finished",
                producer_instance_id="node-1-abc",
                sequence_no=1,
                trace_id="trace_aaa",
                payload={
                    "session_id": "conv_1",
                    "user_id": "user_1",
                    "tenant_id": "tenant_1",
                    "status": "failed",
                    "started_at": datetime.now(UTC).isoformat(),
                },
            )
        ]
    )
    from tianzhou_agent_platform.main import _resolve_obs_trace_status

    status = await _resolve_obs_trace_status(obs, "trace_aaa")
    assert status == "failed"
    assert await _resolve_obs_trace_status(obs, "missing") is None
    await obs.close()
