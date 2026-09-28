from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Generic, Protocol, TypeVar

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    delete,
    insert,
    select,
    text,
    update,
)
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tianzhou_agent_platform.store.database.mysql import MySqlStore
from tianzhou_agent_platform.store.errors import StorageError
from tianzhou_agent_platform.store.redis.client import RedisStore
from tianzhou_agent_platform.tasks.models import SessionTask

T = TypeVar("T")

task_metadata = MetaData()

session_tasks_table = Table(
    "session_tasks",
    task_metadata,
    Column("task_id", String(36), primary_key=True),
    Column("session_id", String(64), nullable=False),
    Column("owner_user_id", String(128), nullable=False),
    Column("title", String(200), nullable=False),
    Column("description", Text, nullable=False),
    Column("status", String(20), nullable=False),
    Column("reason", String(500), nullable=False),
    Column("evidence", JSON, nullable=False),
    Column("verification_status", String(20), nullable=False),
    Column("verification_reason", Text, nullable=False),
    Column("verified_at", DateTime(timezone=True), nullable=True),
    Column("parent_task_id", String(36), nullable=True),
    Column("depth", Integer, nullable=False),
    Column("sort_order", Integer, nullable=False),
    Column("version", Integer, nullable=False),
    Column("idempotency_key", String(200), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("session_id", "idempotency_key", name="uq_session_tasks_session_idempotency"),
    Index("ix_session_tasks_session_parent_sort", "session_id", "parent_task_id", "sort_order"),
)

session_task_meta_table = Table(
    "session_task_meta",
    task_metadata,
    Column("session_id", String(64), primary_key=True),
    Column("revision", BigInteger, nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)


@dataclass(slots=True)
class MutationDecision(Generic[T]):
    value: T
    changed: bool = True


@dataclass(slots=True)
class MutationCommit(Generic[T]):
    value: T
    tasks: list[SessionTask]
    revision: int
    changed: bool


TaskMutator = Callable[[list[SessionTask]], MutationDecision[T]]


class SessionTaskStore(Protocol):
    async def initialize(self) -> None: ...

    async def read(self, session_id: str) -> tuple[list[SessionTask], int]: ...

    async def revision(self, session_id: str) -> int: ...

    async def mutate(
        self,
        session_id: str,
        owner_user_id: str,
        mutator: TaskMutator[T],
    ) -> MutationCommit[T]: ...


class InMemorySessionTaskStore:
    def __init__(self) -> None:
        self._tasks: dict[str, list[SessionTask]] = {}
        self._revisions: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def initialize(self) -> None:
        return None

    async def read(self, session_id: str) -> tuple[list[SessionTask], int]:
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            return self._copy_tasks(self._tasks.get(session_id, [])), self._revisions.get(session_id, 0)

    async def revision(self, session_id: str) -> int:
        return self._revisions.get(session_id, 0)

    async def mutate(
        self,
        session_id: str,
        owner_user_id: str,
        mutator: TaskMutator[T],
    ) -> MutationCommit[T]:
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            tasks = self._copy_tasks(self._tasks.get(session_id, []))
            self._validate_owner(tasks, owner_user_id)
            decision = mutator(tasks)
            revision = self._revisions.get(session_id, 0)
            if decision.changed:
                revision += 1
                self._tasks[session_id] = self._copy_tasks(tasks)
                self._revisions[session_id] = revision
            return MutationCommit(
                value=decision.value,
                tasks=self._copy_tasks(tasks),
                revision=revision,
                changed=decision.changed,
            )

    @staticmethod
    def _copy_tasks(tasks: list[SessionTask]) -> list[SessionTask]:
        return [task.model_copy(deep=True) for task in tasks]

    @staticmethod
    def _validate_owner(tasks: list[SessionTask], owner_user_id: str) -> None:
        if any(task.owner_user_id != owner_user_id for task in tasks):
            raise PermissionError("Task tree ownership does not match the caller")


class MySqlSessionTaskStore:
    def __init__(self, mysql: MySqlStore, redis: RedisStore) -> None:
        self._mysql = mysql
        self._redis = redis
        self._session_factory: async_sessionmaker[Any] = mysql.session_factory

    async def initialize(self) -> None:
        await self._mysql.create_tables(task_metadata)
        await self._upgrade_session_id_columns()

    async def read(self, session_id: str) -> tuple[list[SessionTask], int]:
        async with self._session_factory() as session:
            tasks = await self._read_tasks(session, session_id)
            revision = await self._read_revision(session, session_id)
        return tasks, revision

    async def revision(self, session_id: str) -> int:
        try:
            cached = await self._redis.get("task:revision", session_id)
        except StorageError:
            cached = None
        if cached is not None:
            return int(cached.value)
        async with self._session_factory() as session:
            revision = await self._read_revision(session, session_id)
        await self._cache_revision(session_id, revision)
        return revision

    async def mutate(
        self,
        session_id: str,
        owner_user_id: str,
        mutator: TaskMutator[T],
    ) -> MutationCommit[T]:
        async with self._session_factory() as session:
            async with session.begin():
                now = datetime.now(UTC)
                await session.execute(
                    mysql_insert(session_task_meta_table)
                    .values(session_id=session_id, revision=0, updated_at=now)
                    .on_duplicate_key_update(session_id=session_id)
                )
                meta_result = await session.execute(
                    select(session_task_meta_table)
                    .where(session_task_meta_table.c.session_id == session_id)
                    .with_for_update()
                )
                meta = meta_result.mappings().one()
                current_revision = int(meta["revision"])
                tasks = await self._read_tasks(session, session_id, for_update=True)
                if any(task.owner_user_id != owner_user_id for task in tasks):
                    raise PermissionError("Task tree ownership does not match the caller")
                original = {task.task_id: task for task in tasks}
                decision = mutator(tasks)
                revision = current_revision
                if decision.changed:
                    revision += 1
                    await self._write_tasks(session, session_id, original, tasks)
                    await session.execute(
                        update(session_task_meta_table)
                        .where(session_task_meta_table.c.session_id == session_id)
                        .values(revision=revision, updated_at=now)
                    )
        if decision.changed:
            await self._cache_revision(session_id, revision)
        return MutationCommit(
            value=decision.value,
            tasks=[task.model_copy(deep=True) for task in tasks],
            revision=revision,
            changed=decision.changed,
        )

    async def _read_tasks(
        self,
        session: AsyncSession,
        session_id: str,
        *,
        for_update: bool = False,
    ) -> list[SessionTask]:
        statement = (
            select(session_tasks_table)
            .where(session_tasks_table.c.session_id == session_id)
            .order_by(session_tasks_table.c.depth, session_tasks_table.c.sort_order, session_tasks_table.c.created_at)
        )
        if for_update:
            statement = statement.with_for_update()
        result = await session.execute(statement)
        return [SessionTask.model_validate(dict(row)) for row in result.mappings().all()]

    async def _read_revision(self, session: AsyncSession, session_id: str) -> int:
        result = await session.execute(
            select(session_task_meta_table.c.revision).where(session_task_meta_table.c.session_id == session_id)
        )
        value = result.scalar_one_or_none()
        return int(value or 0)

    async def _write_tasks(
        self,
        session: AsyncSession,
        session_id: str,
        original: dict[str, SessionTask],
        tasks: list[SessionTask],
    ) -> None:
        current = {task.task_id: task for task in tasks}
        removed = set(original) - set(current)
        if removed:
            await session.execute(delete(session_tasks_table).where(session_tasks_table.c.task_id.in_(removed)))
        for task_id, task in current.items():
            values = task.model_dump(mode="python")
            if task_id in original:
                await session.execute(
                    update(session_tasks_table)
                    .where(session_tasks_table.c.task_id == task_id)
                    .values(**values)
                )
            else:
                await session.execute(insert(session_tasks_table).values(**values))

    async def _cache_revision(self, session_id: str, revision: int) -> None:
        try:
            await self._redis.set_max_int("task:revision", session_id, revision)
        except StorageError:
            pass

    async def _upgrade_session_id_columns(self) -> None:
        table_names = {"session_tasks", "session_task_meta"}
        async with self._mysql.engine.begin() as connection:
            result = await connection.execute(
                text(
                    "SELECT table_name, character_maximum_length "
                    "FROM information_schema.columns "
                    "WHERE table_schema = DATABASE() AND column_name = 'session_id' "
                    "AND table_name IN ('session_tasks', 'session_task_meta')"
                )
            )
            for table_name, length in result:
                if table_name in table_names and int(length or 0) < 64:
                    await connection.execute(
                        text(f"ALTER TABLE {table_name} MODIFY session_id VARCHAR(64) NOT NULL")
                    )
