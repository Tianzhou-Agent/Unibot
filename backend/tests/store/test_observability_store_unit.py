from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

import pytest

from tianzhou_agent_platform.store import observability_store as store_module
from tianzhou_agent_platform.store.observability_store import (
    EVENTS_TABLE,
    OBS_METADATA,
    ObservabilityStore,
    _event_values,
    _is_ddl_contention_error,
    _is_duplicate_column_error,
    _is_table_exists_error,
)
from tianzhou_agent_platform.store.observability_wal import ObsRecord


class _ScalarResult:
    def scalar_one(self) -> int:
        return 1


class _FakeConnection:
    def __init__(self, failures: dict[str, int]) -> None:
        self.failures = failures
        self.create_calls: Counter[str] = Counter()

    async def run_sync(self, operation: Any, *args: Any, **kwargs: Any) -> None:
        table_name = operation.__self__.name
        self.create_calls[table_name] += 1
        if self.create_calls[table_name] <= self.failures.get(table_name, 0):
            raise RuntimeError(1684, "table definition is being modified")

    async def execute(self, *args: Any, **kwargs: Any) -> _ScalarResult:
        return _ScalarResult()


class _BeginContext:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    async def __aenter__(self) -> _FakeConnection:
        return self.connection

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeEngine:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def begin(self) -> _BeginContext:
        return _BeginContext(self.connection)


def test_mysql_ddl_errors_require_exact_errno() -> None:
    assert _is_table_exists_error(RuntimeError(1050, "unrelated text"))
    assert _is_duplicate_column_error(RuntimeError(1060, "unrelated text"))
    assert _is_ddl_contention_error(RuntimeError(1684, "unrelated text"))

    assert not _is_table_exists_error(RuntimeError("table already exists"))
    assert not _is_duplicate_column_error(RuntimeError("Duplicate column"))
    assert not _is_ddl_contention_error(RuntimeError("skipped since its definition is being modified"))


def test_event_values_keep_microsecond_and_wal_order() -> None:
    occurred_at = datetime(2026, 8, 10, 7, 0, 0, 123456, tzinfo=timezone.utc)
    values = _event_values(
        ObsRecord(
            record_type="event",
            producer_instance_id="node-1",
            sequence_no=42,
            occurred_at=occurred_at,
            trace_id="trace-1",
            payload={"name": "model.completed", "occurred_at": occurred_at.isoformat()},
        )
    )

    assert values["record_version"] == 1_786_345_200_123_456
    assert values["sequence_no"] == 42


@pytest.mark.asyncio
async def test_create_tables_retries_1684_for_the_current_table(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _FakeConnection({EVENTS_TABLE: 2})
    store = ObservabilityStore(_FakeEngine(connection), None)  # type: ignore[arg-type]

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(store_module.asyncio, "sleep", no_sleep)

    await store.create_tables()

    assert connection.create_calls[EVENTS_TABLE] == 3
    assert set(connection.create_calls) == {table.name for table in OBS_METADATA.sorted_tables}


@pytest.mark.asyncio
async def test_create_tables_raises_after_1684_retries_are_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _FakeConnection({EVENTS_TABLE: 3})
    store = ObservabilityStore(_FakeEngine(connection), None)  # type: ignore[arg-type]

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(store_module.asyncio, "sleep", no_sleep)

    with pytest.raises(RuntimeError, match="1684"):
        await store.create_tables()

    assert connection.create_calls[EVENTS_TABLE] == 3
