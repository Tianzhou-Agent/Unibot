from datetime import datetime, timezone
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, create_engine, inspect, text
from sqlalchemy.dialects import mysql

from tianzhou_agent_platform.store import (
    MySqlStore,
    StorageUnsupportedCapabilityError,
    StorageValidationError,
    StoreCondition,
    StoreQuery,
)
from tianzhou_agent_platform.store.repository_schema import repository_metadata


@pytest.mark.asyncio
async def test_oauth_schema_upgrade_preserves_existing_users_and_is_idempotent() -> None:
    # Use a real SQL engine through the async connection interface used by create_tables.
    engine = create_engine("sqlite://")

    class ConnectionAdapter:
        def __init__(self, connection):
            self.connection = connection

        async def run_sync(self, callback):
            return callback(self.connection)

        async def execute(self, statement):
            return self.connection.execute(statement)

    class EngineAdapter:
        @asynccontextmanager
        async def begin(self):
            with engine.begin() as connection:
                yield ConnectionAdapter(connection)

    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE unibot_users (id VARCHAR(255) PRIMARY KEY, email VARCHAR(320))"))
        connection.execute(text("INSERT INTO unibot_users VALUES ('existing', 'existing@example.com')"))
    store = MySqlStore(EngineAdapter(), fake_session_factory)
    await store.create_tables(repository_metadata)
    await store.create_tables(repository_metadata)
    with engine.begin() as connection:
        columns = inspect(connection).get_columns("unibot_users")
        assert [column["name"] for column in columns].count("huawei_id") == 1
        assert [column["name"] for column in columns].count("oidc_identities") == 1
        row = connection.execute(text("SELECT id, email, huawei_id, oidc_identities FROM unibot_users")).one()
        assert tuple(row) == ("existing", "existing@example.com", None, None)
    engine.dispose()


class FakeEngine:
    async def dispose(self) -> None:
        return None


def fake_session_factory():
    raise AssertionError("session factory should not be used by validation-only tests")


class FakeQueryResult:
    def mappings(self) -> "FakeQueryResult":
        return self

    def all(self) -> list[dict[str, object]]:
        return []


class CapturingSession:
    def __init__(self, statements: list[object]) -> None:
        self._statements = statements

    async def __aenter__(self) -> "CapturingSession":
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        return None

    async def execute(self, statement: object) -> FakeQueryResult:
        self._statements.append(statement)
        return FakeQueryResult()


def capturing_session_factory(statements: list[object]):
    def factory() -> CapturingSession:
        return CapturingSession(statements)

    return factory


@pytest.mark.asyncio
async def test_mysql_store_rejects_unconfigured_resource() -> None:
    store = MySqlStore(FakeEngine(), fake_session_factory)

    with pytest.raises(StorageUnsupportedCapabilityError):
        await store.read("missing", 1)


@pytest.mark.asyncio
async def test_mysql_store_rejects_unknown_query_filter() -> None:
    metadata = MetaData()
    table = Table("items", metadata, Column("id", Integer), Column("name", String))
    store = MySqlStore(FakeEngine(), fake_session_factory, resource_tables={"items": table})

    with pytest.raises(StorageValidationError):
        await store.query("items", StoreQuery(filters={"unknown": "value"}))


@pytest.mark.asyncio
async def test_mysql_store_applies_conditions() -> None:
    statements: list[object] = []
    metadata = MetaData()
    table = Table(
        "items",
        metadata,
        Column("id", Integer),
        Column("status", String),
        Column("created_at", DateTime(timezone=True)),
    )
    store = MySqlStore(FakeEngine(), capturing_session_factory(statements), resource_tables={"items": table})

    page = await store.query(
        "items",
        StoreQuery(
            conditions=[
                StoreCondition(field="created_at", op="ge", value=datetime(2026, 6, 1, tzinfo=timezone.utc)),
                StoreCondition(field="created_at", op="lt", value=datetime(2026, 6, 2, tzinfo=timezone.utc)),
                StoreCondition(field="status", op="ne", value="archived"),
            ]
        ),
    )

    assert page.items == []
    compiled = str(statements[0].compile(dialect=mysql.dialect(), compile_kwargs={"literal_binds": True}))
    assert "items.created_at >= '2026-06-01" in compiled
    assert "items.created_at < '2026-06-02" in compiled
    assert "items.status != 'archived'" in compiled


@pytest.mark.asyncio
async def test_mysql_store_rejects_unknown_condition_field() -> None:
    metadata = MetaData()
    table = Table("items", metadata, Column("id", Integer), Column("name", String))
    store = MySqlStore(FakeEngine(), fake_session_factory, resource_tables={"items": table})

    with pytest.raises(StorageValidationError):
        await store.query(
            "items",
            StoreQuery(conditions=[StoreCondition(field="unknown", op="eq", value="value")]),
        )
