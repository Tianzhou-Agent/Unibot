"""Cross-node exclusion of the three ``LeaseStore`` users against a live Redis and MySQL.

Each "node" is a full ``create_app(storage_settings=...)`` composition with its own ``RedisStore`` and its own
``PersistentRepository``, sharing only the live MySQL and Redis. The apps are built but their lifespan is not run, so
no background worker competes for the keys under test.

Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from tianzhou_agent_platform.aina.document.task_service import DocumentEditWorker
from tianzhou_agent_platform.aina.scheduler import ScheduledAinaTaskCreate
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.conversations.schemas import ChatResponse
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.sandbox.models import SandboxEnsureRequest
from tianzhou_agent_platform.sandbox.service import _actor_digest
from tianzhou_agent_platform.store import PersistentRepository, RedisStore, StorageSettings

pytestmark = pytest.mark.skipif(
    os.getenv("TZ_STORAGE_E2E") != "1",
    reason="Set TZ_STORAGE_E2E=1 and start backend/docker-compose.storage.yml to run live storage E2E tests",
)


@dataclass
class Node:
    name: str
    app: FastAPI

    @property
    def redis(self) -> RedisStore:
        return self.app.state.storage_stores.redis

    @property
    def repository(self) -> PersistentRepository:
        return self.app.state.repository

    @property
    def scheduler(self) -> Any:
        return self.app.state.aina_scheduler

    @property
    def sandbox(self) -> Any:
        return self.app.state.sandbox_service

    @property
    def documents(self) -> Any:
        return self.app.state.document_edit_task_service


def _storage_settings(tmp_path: Path) -> StorageSettings:
    return StorageSettings(
        mysql_dsn=SecretStr(
            os.getenv("TZ_STORAGE_E2E_MYSQL_DSN", "mysql+aiomysql://unibot:unibot@127.0.0.1:13306/unibot")
        ),
        redis_dsn=SecretStr(os.getenv("TZ_STORAGE_E2E_REDIS_URL", "redis://127.0.0.1:16379/0")),
        nas_root_path=tmp_path / "nas",
    )


def _node(name: str, tmp_path: Path) -> Node:
    app = create_app(
        settings=AgentSettings(
            _env_file=None,
            obs_enabled=False,
            node_id=name,
            sandbox_driver="local",
            sandbox_workspace_root=tmp_path / name / "sandbox",
        ),
        storage_settings=_storage_settings(tmp_path),
    )
    return Node(name, app)


@asynccontextmanager
async def _two_nodes(tmp_path: Path) -> AsyncIterator[tuple[Node, Node]]:
    first, second = _node("node-a", tmp_path), _node("node-b", tmp_path)
    try:
        await first.repository.initialize()
        await second.repository.initialize()
        yield first, second
    finally:
        await first.app.state.storage_stores.close()
        await second.app.state.storage_stores.close()


async def _pttl_ms(node: Node, namespace: str, key: str) -> int:
    # -2: no such key, -1: the key never expires.
    return int(await node.redis._client.pttl(node.redis._redis_key(namespace, key)))


async def _expire_in(node: Node, namespace: str, key: str, milliseconds: int) -> None:
    """Fast-forward a held lease's expiry, as when the node holding it died and nothing renews or releases it."""
    assert await node.redis._client.pexpire(node.redis._redis_key(namespace, key), milliseconds)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


async def test_the_app_hands_one_real_redis_store_to_all_three_lease_users(tmp_path: Path) -> None:
    async with _two_nodes(tmp_path) as (first, second):
        for node in (first, second):
            redis = node.app.state.storage_stores.redis
            assert isinstance(redis, RedisStore)
            assert node.scheduler.leases is redis
            assert node.sandbox.leases is redis
            assert node.documents.leases is redis
            assert node.repository.stores is node.app.state.storage_stores
            # The store the services share is connected to the live Redis.
            assert await redis.exists("lease-wiring", _unique("absent")) is False
        # The nodes are independent: separate Redis clients, separate repositories, separate service objects.
        assert first.redis is not second.redis
        assert first.repository is not second.repository
        assert first.scheduler is not second.scheduler
        assert first.sandbox is not second.sandbox
        assert first.documents is not second.documents


@pytest.mark.parametrize("namespace", ["aina-schedule-lease", "aina-schedule-run"])
async def test_scheduler_lease_has_one_winner_across_nodes_and_is_released(tmp_path: Path, namespace: str) -> None:
    key = _unique("scheduler")
    async with _two_nodes(tmp_path) as (first, second):
        holding, release = asyncio.Event(), asyncio.Event()

        async def hold() -> bool:
            async with first.scheduler._lease(namespace, key, ttl_seconds=60) as acquired:
                holding.set()
                await release.wait()
                return acquired

        holder = asyncio.create_task(hold())
        await asyncio.wait_for(holding.wait(), timeout=5)
        try:
            async with second.scheduler._lease(namespace, key, ttl_seconds=60) as contender:
                assert contender is False
            assert 0 < await _pttl_ms(first, namespace, key) <= 60_000
        finally:
            release.set()
        assert await holder is True

        assert await _pttl_ms(first, namespace, key) == -2  # released, not left to expire
        async with second.scheduler._lease(namespace, key, ttl_seconds=60) as later:
            assert later is True


async def test_scheduler_lease_expires_when_its_holder_dies(tmp_path: Path) -> None:
    namespace, key = "aina-schedule-run", _unique("scheduler-expiry")
    async with _two_nodes(tmp_path) as (first, second):
        async with first.scheduler._lease(namespace, key, ttl_seconds=60) as acquired:
            assert acquired is True
            async with second.scheduler._lease(namespace, key, ttl_seconds=60) as contender:
                assert contender is False
            await _expire_in(first, namespace, key, 300)
            await asyncio.sleep(0.6)
            async with second.scheduler._lease(namespace, key, ttl_seconds=60) as taken_over:
                assert taken_over is True


async def test_a_due_task_runs_once_across_nodes_and_manual_runs_are_refused_meanwhile(tmp_path: Path) -> None:
    async with _two_nodes(tmp_path) as (first, second):
        prompt = _unique("live-lease-prompt")
        started, release = asyncio.Event(), asyncio.Event()
        calls: list[tuple[str, str]] = []

        def agent_for(node: Node):  # type: ignore[no-untyped-def]
            async def agent(request):  # type: ignore[no-untyped-def]
                calls.append((node.name, request.message))
                started.set()
                await release.wait()
                return ChatResponse(
                    conversation_id="conversation-test",
                    content="Done",
                    status="completed",
                    trace_id="trace-test",
                    iterations=1,
                )

            return agent

        task = await first.repository.create_scheduled_aina_task(
            ScheduledAinaTaskCreate(name=_unique("live lease task"), prompt=prompt)
        )
        try:
            task = await first.repository.put_scheduled_aina_task(
                task.model_copy(update={"next_run_at": datetime.now(UTC) - timedelta(seconds=5)})
            )
            now = datetime.now(UTC)
            for node in (first, second):
                node.scheduler.chat_service = SimpleNamespace(run_turn=agent_for(node))
                original = node.repository.list_due_scheduled_aina_tasks

                async def only_this_task(at, original=original):  # type: ignore[no-untyped-def]
                    return [item for item in await original(at) if item.id == task.id]

                node.repository.list_due_scheduled_aina_tasks = only_this_task  # type: ignore[method-assign]

            running = asyncio.create_task(first.scheduler.tick(now=now))
            try:
                await asyncio.wait_for(started.wait(), timeout=10)
                await second.scheduler.tick(now=now)  # sees the same due task through its own repository
                with pytest.raises(PlatformError) as refused:
                    await second.scheduler.run_now(task.id)
                assert refused.value.status_code == 409
                assert calls == [("node-a", prompt)]
            finally:
                release.set()
                await running

            executions = await second.repository.list_scheduled_aina_executions(task.id)
            assert [(item.node_id, item.trigger) for item in executions] == [("node-a", "scheduled")]

            # The run lease was released with the run, so the other node can now run it by hand.
            await second.scheduler.run_now(task.id)
            assert calls == [("node-a", prompt), ("node-b", prompt)]
        finally:
            await first.repository.remove_scheduled_aina_task(task.id)


async def test_sandbox_operations_on_one_actor_exclude_each_other_across_nodes(tmp_path: Path) -> None:
    user, tenant = _unique("user"), _unique("tenant")
    key = _actor_digest(tenant, user, None, length=None)
    namespace = "sandbox-actor-operation"
    async with _two_nodes(tmp_path) as (first, second):
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold() -> None:
            async with first.sandbox._actor_lease(user, tenant, None):
                entered.set()
                await release.wait()

        holder = asyncio.create_task(hold())
        await asyncio.wait_for(entered.wait(), timeout=5)
        try:
            assert 0 < await _pttl_ms(first, namespace, key) <= 60_000
            request = SandboxEnsureRequest(user_id=user, tenant_id=tenant)
            # Each node has its own in-process lock, so only Redis can keep the second node out.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(second.sandbox.ensure(request), timeout=1.5)
            other_actor = SandboxEnsureRequest(user_id=_unique("user"), tenant_id=tenant)
            assert (await asyncio.wait_for(second.sandbox.ensure(other_actor), timeout=10)).status == "ready"

            release_later = asyncio.get_running_loop().call_later(0.5, release.set)
            record = await asyncio.wait_for(second.sandbox.ensure(request), timeout=10)  # waits, then proceeds
            assert record.status == "ready"
            release_later.cancel()
        finally:
            release.set()
            await holder
        assert await _pttl_ms(first, namespace, key) == -2

        async with second.sandbox._actor_lease(user, tenant, None):
            pass


async def test_a_sandbox_operation_gives_up_with_a_conflict_while_another_node_holds_the_actor(
    tmp_path: Path,
) -> None:
    user, tenant = _unique("user"), _unique("tenant")
    async with _two_nodes(tmp_path) as (first, second):
        async with first.sandbox._actor_lease(user, tenant, None):
            with pytest.raises(PlatformError) as refused:
                await second.sandbox.ensure(SandboxEnsureRequest(user_id=user, tenant_id=tenant))
        assert refused.value.status_code == 409
        assert refused.value.retryable is True
        assert (await second.sandbox.ensure(SandboxEnsureRequest(user_id=user, tenant_id=tenant))).status == "ready"


async def test_sandbox_actor_lease_expires_when_its_holder_dies(tmp_path: Path) -> None:
    user, tenant = _unique("user"), _unique("tenant")
    key = _actor_digest(tenant, user, None, length=None)
    async with _two_nodes(tmp_path) as (first, second):
        async with first.sandbox._actor_lease(user, tenant, None):
            await _expire_in(first, "sandbox-actor-operation", key, 300)
            await asyncio.sleep(0.6)
            await asyncio.wait_for(second.sandbox.ensure(SandboxEnsureRequest(user_id=user, tenant_id=tenant)), 10)


async def test_document_merge_lock_has_one_winner_across_nodes_and_is_released(tmp_path: Path) -> None:
    key = _unique("tenant:user:standalone:doc.md")
    async with _two_nodes(tmp_path) as (first, second):
        results = await asyncio.gather(
            first.documents._acquire_merge_lock(key), second.documents._acquire_merge_lock(key)
        )
        assert sorted(results) == [False, True]
        winner, loser = (first, second) if results[0] else (second, first)
        assert 0 < await _pttl_ms(winner, "document-merge", key) <= 15 * 60 * 1000

        await winner.documents._release_merge_lock(key)
        assert await _pttl_ms(winner, "document-merge", key) == -2
        assert await loser.documents._acquire_merge_lock(key) is True
        # A late release by the other node cannot be mistaken for ownership: the key is simply the shared lock.
        assert await winner.documents._acquire_merge_lock(key) is False
        await loser.documents._release_merge_lock(key)


async def test_document_merge_lock_expires_when_its_holder_dies(tmp_path: Path) -> None:
    key = _unique("tenant:user:standalone:expiry.md")
    async with _two_nodes(tmp_path) as (first, second):
        assert await first.documents._acquire_merge_lock(key) is True
        assert await second.documents._acquire_merge_lock(key) is False
        await _expire_in(first, "document-merge", key, 300)
        await asyncio.sleep(0.6)
        assert await second.documents._acquire_merge_lock(key) is True
        await second.documents._release_merge_lock(key)


async def test_document_edit_worker_claim_has_one_winner_across_nodes_and_is_visible_to_both(tmp_path: Path) -> None:
    task_id = _unique("task")
    async with _two_nodes(tmp_path) as (first, second):
        worker_a, worker_b = DocumentEditWorker(first.documents), DocumentEditWorker(second.documents)
        assert await worker_a._claim_exists(task_id) is False
        assert await worker_b._claim_exists(task_id) is False

        results = await asyncio.gather(worker_a._claim(task_id), worker_b._claim(task_id))
        assert sorted(results) == [False, True]
        winner, loser = (worker_a, worker_b) if results[0] else (worker_b, worker_a)
        # The other node must see a live claim, or it would reset a task this node is still generating.
        assert await loser._claim_exists(task_id) is True
        assert 0 < await _pttl_ms(first, "document-edit-worker", task_id) <= 15 * 60 * 1000

        await winner._release(task_id)
        assert await loser._claim_exists(task_id) is False
        assert await loser._claim(task_id) is True
        await loser._release(task_id)


async def test_document_edit_worker_claim_expires_when_its_holder_dies(tmp_path: Path) -> None:
    task_id = _unique("task")
    async with _two_nodes(tmp_path) as (first, second):
        worker_a, worker_b = DocumentEditWorker(first.documents), DocumentEditWorker(second.documents)
        assert await worker_a._claim(task_id) is True
        assert await worker_b._claim(task_id) is False
        await _expire_in(first, "document-edit-worker", task_id, 300)
        await asyncio.sleep(0.6)
        assert await worker_b._claim_exists(task_id) is False
        assert await worker_b._claim(task_id) is True
        await worker_b._release(task_id)
