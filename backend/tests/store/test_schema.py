from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from tianzhou_agent_platform.store.schema import APP_METADATA, SCHEMA_TABLE, ensure_schema

OWNED_TABLES = {table for metadata in APP_METADATA for table in metadata.tables}


def _tables(path: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as connection:
            return set(inspect(connection).get_table_names())
    finally:
        engine.dispose()


def _execute(path: Path, statement: str) -> list[tuple]:
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.begin() as connection:
            result = connection.execute(text(statement))
            return [tuple(row) for row in result] if result.returns_rows else []
    finally:
        engine.dispose()


async def _ensure(path: Path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    try:
        await ensure_schema(engine)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_ensure_schema_creates_every_owned_table_and_keeps_data_while_the_schema_matches(tmp_path: Path) -> None:
    path = tmp_path / "unibot.db"

    await _ensure(path)
    assert _tables(path) == OWNED_TABLES | {SCHEMA_TABLE.name}

    _execute(path, "INSERT INTO storage_runtime_probe (name, status) VALUES ('kept', 'ok')")
    await _ensure(path)
    assert _execute(path, "SELECT name FROM storage_runtime_probe") == [("kept",)]


@pytest.mark.asyncio
async def test_ensure_schema_drops_and_recreates_tables_from_an_older_schema(tmp_path: Path) -> None:
    path = tmp_path / "unibot.db"
    _execute(path, "CREATE TABLE unibot_users (id VARCHAR(255) PRIMARY KEY, payload TEXT)")
    _execute(path, "INSERT INTO unibot_users VALUES ('old-user', '{}')")
    _execute(path, "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
    _execute(path, "CREATE TABLE someone_elses_table (id INTEGER PRIMARY KEY)")

    await _ensure(path)

    assert _tables(path) == OWNED_TABLES | {SCHEMA_TABLE.name, "someone_elses_table"}
    assert _execute(path, "SELECT COUNT(*) FROM unibot_users") == [(0,)]

    # A stored fingerprint from different table definitions also resets the schema.
    _execute(path, "INSERT INTO storage_runtime_probe (name, status) VALUES ('dropped', 'ok')")
    _execute(path, f"UPDATE {SCHEMA_TABLE.name} SET fingerprint = 'stale'")
    await _ensure(path)
    assert _execute(path, "SELECT COUNT(*) FROM storage_runtime_probe") == [(0,)]
    assert _execute(path, f"SELECT COUNT(*) FROM {SCHEMA_TABLE.name}") == [(1,)]
