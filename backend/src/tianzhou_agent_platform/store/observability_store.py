"""OBS MySQL schema, dedicated connection pool and idempotent bulk UPSERT.

Design (section 9, 10, 11 of ir-01): Trace/Span/Event are normalized rows,
statistics are stored as absolute values (never accumulated on replay),
writes use INSERT ... ON DUPLICATE KEY UPDATE in one transaction per batch,
and the store never performs a SELECT after writing.

The store uses its own small connection pool (pool_size=2, max_overflow=2 by
default) so OBS batches cannot exhaust the business connection pool.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Column,
    DateTime,
    Double,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    delete as sa_delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from tianzhou_agent_platform.store.observability_wal import ObsRecord, RecordType

logger = logging.getLogger(__name__)

OBS_METADATA = MetaData()

TRACES_TABLE = "unibot_obs_traces"
SPANS_TABLE = "unibot_obs_spans"
EVENTS_TABLE = "unibot_obs_events"


def _build_traces_table() -> Table:
    table = Table(
        TRACES_TABLE,
        OBS_METADATA,
        Column("trace_id", String(64), primary_key=True),
        Column("legacy_trace_id", String(64), nullable=True),
        Column("root_span_id", String(32), nullable=True),
        Column("session_id", String(160), nullable=True),
        Column("user_id", String(160), nullable=False),
        Column("tenant_id", String(160), nullable=False),
        Column("producer_instance_id", String(255), nullable=True),
        Column("status", String(32), nullable=False),
        Column("started_at", DateTime(timezone=True), nullable=False),
        Column("completed_at", DateTime(timezone=True), nullable=True),
        Column("duration_ms", Double(), nullable=True),
        Column("input_tokens", BigInteger(), nullable=False, server_default="0"),
        Column("output_tokens", BigInteger(), nullable=False, server_default="0"),
        Column("cache_read_tokens", BigInteger(), nullable=False, server_default="0"),
        Column("message_count", Integer(), nullable=False, server_default="0"),
        Column("compression_count", Integer(), nullable=False, server_default="0"),
        Column("error_count", Integer(), nullable=False, server_default="0"),
        # monotonic per-record version (occurred_at microseconds): an UPSERT
        # only applies when the incoming record is newer, so out-of-order
        # finished records cannot overwrite a newer terminal state (P1-2)
        Column("record_version", BigInteger(), nullable=False, server_default="0"),
        Column("attributes", JSON, nullable=True),
        Index("idx_obs_traces_tenant_user_time", "tenant_id", "user_id", "started_at"),
        Index("idx_obs_traces_tenant_session_time", "tenant_id", "session_id", "started_at"),
        Index("idx_obs_traces_status_time", "status", "started_at"),
        Index("idx_obs_traces_legacy_id", "legacy_trace_id", unique=True),
    )
    return table


def _build_spans_table() -> Table:
    table = Table(
        SPANS_TABLE,
        OBS_METADATA,
        Column("span_id", String(32), primary_key=True),
        Column("legacy_span_id", String(64), nullable=True),
        Column("trace_id", String(64), nullable=False),
        Column("parent_span_id", String(32), nullable=True),
        Column("sequence_no", BigInteger(), nullable=False),
        Column("session_id", String(160), nullable=True),
        Column("user_id", String(160), nullable=False),
        Column("tenant_id", String(160), nullable=False),
        Column("kind", String(32), nullable=False),
        Column("name", String(255), nullable=False),
        Column("target_id", String(255), nullable=True),
        Column("model", String(255), nullable=True),
        Column("status", String(32), nullable=False),
        Column("started_at", DateTime(timezone=True), nullable=False),
        Column("first_output_at", DateTime(timezone=True), nullable=True),
        Column("completed_at", DateTime(timezone=True), nullable=True),
        Column("duration_ms", Double(), nullable=True),
        Column("ttft_ms", Double(), nullable=True),
        Column("input_tokens", BigInteger(), nullable=False, server_default="0"),
        Column("output_tokens", BigInteger(), nullable=False, server_default="0"),
        Column("cache_read_tokens", BigInteger(), nullable=False, server_default="0"),
        Column("input_preview", Text(), nullable=True),
        Column("output_preview", Text(), nullable=True),
        # per-record version guard (same semantics as unibot_obs_traces)
        Column("record_version", BigInteger(), nullable=False, server_default="0"),
        Column("attributes", JSON, nullable=True),
        Column("error", JSON, nullable=True),
        Column("raw_io_path", String(1024), nullable=True),
        Column("raw_io_sha256", String(64), nullable=True),
        Column("raw_io_size_bytes", BigInteger(), nullable=True),
        Column("raw_io_status", String(32), nullable=False, server_default="not_applicable"),
        Index("idx_obs_spans_trace_seq", "trace_id", "sequence_no"),
        Index("idx_obs_spans_trace_parent", "trace_id", "parent_span_id"),
        Index("idx_obs_spans_tenant_user_time", "tenant_id", "user_id", "started_at"),
        Index("idx_obs_spans_tenant_session_time", "tenant_id", "session_id", "started_at"),
        Index("idx_obs_spans_kind_model_time", "kind", "model", "started_at"),
        Index("idx_obs_spans_status_time", "status", "started_at"),
        Index("idx_obs_spans_legacy_id", "legacy_span_id", unique=True),
    )
    return table


def _build_events_table() -> Table:
    table = Table(
        EVENTS_TABLE,
        OBS_METADATA,
        Column("event_id", String(64), primary_key=True),
        Column("trace_id", String(64), nullable=False),
        Column("span_id", String(32), nullable=True),
        Column("session_id", String(160), nullable=True),
        Column("user_id", String(160), nullable=False),
        Column("tenant_id", String(160), nullable=False),
        Column("name", String(255), nullable=False),
        Column("status", String(32), nullable=True),
        Column("occurred_at", DateTime(timezone=True), nullable=False),
        # Microsecond event order survives MySQL DATETIME's default
        # second-level precision and remains valid across producer restarts.
        Column("record_version", BigInteger(), nullable=False, server_default="0"),
        # WAL order only breaks ties inside the same microsecond; it is not
        # used as the primary key because it restarts per producer instance.
        Column("sequence_no", BigInteger(), nullable=False, server_default="0"),
        Column("attributes", JSON, nullable=True),
        Index("idx_obs_events_trace_time", "trace_id", "occurred_at"),
        Index("idx_obs_events_tenant_user_time", "tenant_id", "user_id", "occurred_at"),
    )
    return table


OBS_TABLES: dict[str, Table] = {
    TRACES_TABLE: _build_traces_table(),
    SPANS_TABLE: _build_spans_table(),
    EVENTS_TABLE: _build_events_table(),
}


def _as_datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"cannot convert {type(value)} to datetime")


def _record_version(record: ObsRecord) -> int:
    """Monotonic per-record version: occurred_at in microseconds (UTC)."""
    return int(record.occurred_at.timestamp() * 1_000_000)


def _is_duplicate_column_error(exc: Exception) -> bool:
    """MySQL errno 1060 (duplicate column) means another instance already
    ran the migration."""
    return _mysql_errno_in_chain(exc, {1060})


def _is_table_exists_error(exc: Exception) -> bool:
    """MySQL errno 1050 (table already exists) means another instance already
    created the tables during a concurrent cold start."""
    return _mysql_errno_in_chain(exc, {1050})


def _is_ddl_contention_error(exc: Exception) -> bool:
    """MySQL errno 1684: table skipped because a concurrent DDL statement is
    modifying its definition — transient, safe to retry."""
    return _mysql_errno_in_chain(exc, {1684})


def _mysql_errno_in_chain(exc: Exception, errnos: set[int]) -> bool:
    cursor: Any = exc
    while cursor is not None:
        args = getattr(cursor, "args", None)
        if args:
            for arg in args:
                if isinstance(arg, int) and arg in errnos:
                    return True
        cursor = getattr(cursor, "__cause__", None) or getattr(cursor, "orig", None)
    return False


def _trace_values(record: ObsRecord) -> dict[str, Any]:
    payload = record.payload
    return {
        "trace_id": record.trace_id,
        "legacy_trace_id": payload.get("legacy_trace_id"),
        "root_span_id": payload.get("root_span_id"),
        "session_id": payload.get("session_id"),
        "user_id": payload.get("user_id"),
        "tenant_id": payload.get("tenant_id"),
        "producer_instance_id": record.producer_instance_id,
        "status": payload.get("status") or "running",
        "started_at": _as_datetime(payload.get("started_at")) or record.occurred_at,
        "completed_at": _as_datetime(payload.get("completed_at")),
        "duration_ms": payload.get("duration_ms"),
        "input_tokens": payload.get("input_tokens") or 0,
        "output_tokens": payload.get("output_tokens") or 0,
        "cache_read_tokens": payload.get("cache_read_tokens") or 0,
        "message_count": payload.get("message_count") or 0,
        "compression_count": payload.get("compression_count") or 0,
        "error_count": payload.get("error_count") or 0,
        "record_version": _record_version(record),
        "attributes": payload.get("attributes"),
    }


def _span_values(record: ObsRecord) -> dict[str, Any]:
    payload = record.payload
    return {
        "span_id": record.span_id or "",
        "legacy_span_id": payload.get("legacy_span_id"),
        "trace_id": record.trace_id,
        "parent_span_id": payload.get("parent_span_id"),
        "sequence_no": payload.get("sequence_no") or 0,
        "session_id": payload.get("session_id"),
        "user_id": payload.get("user_id"),
        "tenant_id": payload.get("tenant_id"),
        "kind": payload.get("kind") or "internal",
        "name": payload.get("name") or "",
        "target_id": payload.get("target_id"),
        "model": payload.get("model"),
        "status": payload.get("status") or "running",
        "started_at": _as_datetime(payload.get("started_at")) or record.occurred_at,
        "first_output_at": _as_datetime(payload.get("first_output_at")),
        "completed_at": _as_datetime(payload.get("completed_at")),
        "duration_ms": payload.get("duration_ms"),
        "ttft_ms": payload.get("ttft_ms"),
        "input_tokens": payload.get("input_tokens") or 0,
        "output_tokens": payload.get("output_tokens") or 0,
        "cache_read_tokens": payload.get("cache_read_tokens") or 0,
        "input_preview": payload.get("input_preview"),
        "output_preview": payload.get("output_preview"),
        "record_version": _record_version(record),
        "attributes": payload.get("attributes"),
        "error": payload.get("error"),
        "raw_io_path": payload.get("raw_io_path"),
        "raw_io_sha256": payload.get("raw_io_sha256"),
        "raw_io_size_bytes": payload.get("raw_io_size_bytes"),
        "raw_io_status": payload.get("raw_io_status") or "not_applicable",
    }


def _event_values(record: ObsRecord) -> dict[str, Any]:
    payload = record.payload
    return {
        "event_id": record.record_id,
        "trace_id": record.trace_id,
        "span_id": record.span_id,
        "session_id": payload.get("session_id"),
        "user_id": payload.get("user_id"),
        "tenant_id": payload.get("tenant_id"),
        "name": payload.get("name") or "event",
        "status": payload.get("status"),
        "occurred_at": _as_datetime(payload.get("occurred_at")) or record.occurred_at,
        "record_version": _record_version(record),
        "sequence_no": record.sequence_no,
        "attributes": payload.get("attributes"),
    }


def _upsert_statement(
    table: Table,
    rows: list[dict[str, Any]],
    *,
    ignore: bool = False,
) -> Any:
    """MySQL ``INSERT ... ON DUPLICATE KEY UPDATE`` with absolute values.

    Statistical columns are always overwritten with the incoming absolute
    value; replaying a segment therefore never doubles token counts.

    ``ignore=True`` (used for *started* records) emits ``INSERT IGNORE`` so a
    late-arriving ``running`` record can never downgrade an already finished
    Trace/Span (review P1-3); finished records fully overwrite instead.
    """
    if ignore:
        return mysql_insert(table).values(rows).prefix_with("IGNORE")
    statement = mysql_insert(table).values(rows)
    update_columns: dict[str, Any] = {}
    for column in table.columns:
        if column.name in table.primary_key.columns.keys():
            continue
        new_value = getattr(statement.inserted, column.name)
        if "record_version" in table.c:
            # Only accept a newer record: out-of-order finished records must
            # not overwrite a newer terminal state (review round 2, P1-2).
            # record_version itself is guarded by the same comparison.
            update_columns[column.name] = func.if_(
                statement.inserted.record_version >= table.c.record_version,
                new_value,
                table.c[column.name],
            )
        else:
            update_columns[column.name] = new_value
    return statement.on_duplicate_key_update(**update_columns)


class ObservabilityStore:
    """Dedicated OBS tables, independent pool, batch UPSERT and queries."""

    def __init__(self, engine: AsyncEngine, session_factory: async_sessionmaker[Any]) -> None:
        self._engine = engine
        self._session_factory = session_factory
        self.tables = OBS_TABLES

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        pool_size: int = 2,
        max_overflow: int = 2,
    ) -> "ObservabilityStore":
        engine = create_async_engine(dsn, pool_size=pool_size, max_overflow=max_overflow)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return cls(engine, session_factory)

    async def create_tables(self) -> None:
        async with self._engine.begin() as connection:
            # create tables one by one so a concurrent cold start (another
            # instance creating the same table -> errno 1050) only skips the
            # table that raced, instead of aborting the remaining CREATEs
            # (review round 5, P1)
            for table in OBS_METADATA.sorted_tables:
                for attempt in range(3):
                    try:
                        await connection.run_sync(table.create, checkfirst=True)
                        break
                    except Exception as exc:  # noqa: BLE001
                        if _is_table_exists_error(exc):
                            logger.info("Table %s already created by another instance", table.name)
                            break
                        if _is_ddl_contention_error(exc) and attempt < 2:
                            await asyncio.sleep(0.2 * (attempt + 1))
                            continue
                        raise
            # SQLAlchemy create_all() never alters existing tables: migrate the
            # record_version columns explicitly so databases created by an
            # earlier schema keep working after upgrade (review round 3, P0).
            await self._ensure_schema_columns(connection)

    async def _ensure_schema_columns(self, connection: Any) -> None:
        # Concurrent upgrades are tolerated: errno 1060 (duplicate column)
        # means another instance already migrated; errno 1684 (table skipped
        # by concurrent DDL) is transient metadata-lock contention and is
        # retried (review round 4/5, P1).
        for attempt in range(3):
            try:
                migrations = [
                    (TRACES_TABLE, "record_version"),
                    (SPANS_TABLE, "record_version"),
                    (EVENTS_TABLE, "record_version"),
                    (EVENTS_TABLE, "sequence_no"),
                ]
                for table_name, column_name in migrations:
                    result = await connection.execute(
                        text(
                            "SELECT COUNT(*) FROM information_schema.columns "
                            "WHERE table_schema = DATABASE() AND table_name = :table AND column_name = :column"
                        ),
                        {"table": table_name, "column": column_name},
                    )
                    if int(result.scalar_one()) > 0:
                        continue
                    try:
                        # table names come from module constants, never user input
                        await connection.execute(
                            text(
                                f"ALTER TABLE {table_name} "
                                f"ADD COLUMN {column_name} BIGINT NOT NULL DEFAULT 0"
                            )
                        )
                        logger.info("Migrated %s: added %s column", table_name, column_name)
                    except Exception as exc:  # noqa: BLE001
                        if _is_duplicate_column_error(exc):
                            logger.info("%s already migrated by another instance", table_name)
                            continue
                        raise
                return
            except Exception as exc:  # noqa: BLE001
                if _is_ddl_contention_error(exc) and attempt < 2:
                    await asyncio.sleep(0.2 * (attempt + 1))
                    continue
                raise

    async def bulk_upsert(self, records: list[ObsRecord]) -> int:
        """Persist a batch of WAL records in one transaction. Returns row count."""
        if not records:
            return 0
        grouped: dict[RecordType, list[dict[str, Any]]] = {
            "trace_started": [],
            "trace_finished": [],
            "span_started": [],
            "span_finished": [],
            "event": [],
        }
        for record in records:
            if record.record_type in ("trace_started", "trace_finished"):
                grouped[record.record_type].append(_trace_values(record))
            elif record.record_type in ("span_started", "span_finished"):
                grouped[record.record_type].append(_span_values(record))
            elif record.record_type == "event":
                grouped["event"].append(_event_values(record))

        total = 0
        async with self._session_factory() as session:
            async with session.begin():
                for record_type, rows in grouped.items():
                    if not rows:
                        continue
                    table = self.tables[TRACES_TABLE] if record_type in ("trace_started", "trace_finished") else (
                        self.tables[SPANS_TABLE] if record_type in ("span_started", "span_finished") else self.tables[EVENTS_TABLE]
                    )
                    ignore = record_type in ("trace_started", "span_started")
                    result = await session.execute(_upsert_statement(table, rows, ignore=ignore))
                    total += result.rowcount or 0
        return total

    # ---- queries (used by ObsQueryService; design section 12) ----

    async def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        table = self.tables[TRACES_TABLE]
        async with self._session_factory() as session:
            result = await session.execute(select(table).where(table.c.trace_id == trace_id))
            row = result.mappings().first()
            return dict(row) if row is not None else None

    async def fail_interrupted_producers(
        self,
        producer_instance_ids: list[str],
        *,
        interrupted_at: datetime,
    ) -> dict[str, int]:
        """Terminalize running rows owned by producers proven stale."""
        if not producer_instance_ids:
            return {"traces": 0, "spans": 0}
        trace_table = self.tables[TRACES_TABLE]
        span_table = self.tables[SPANS_TABLE]
        version = int(interrupted_at.timestamp() * 1_000_000)
        reason_path = '$."unibot.interruption.reason"'
        async with self._session_factory() as session:
            async with session.begin():
                traces = await session.execute(
                    update(trace_table)
                    .where(trace_table.c.producer_instance_id.in_(producer_instance_ids))
                    .where(trace_table.c.status == "running")
                    .values(
                        status="failed",
                        completed_at=interrupted_at,
                        record_version=version,
                        attributes=func.JSON_SET(
                            func.coalesce(trace_table.c.attributes, func.JSON_OBJECT()),
                            reason_path,
                            "process_restart",
                        ),
                    )
                )
                spans = await session.execute(
                    update(span_table)
                    .where(span_table.c.producer_instance_id.in_(producer_instance_ids))
                    .where(span_table.c.status == "running")
                    .values(
                        status="failed",
                        completed_at=interrupted_at,
                        record_version=version,
                        attributes=func.JSON_SET(
                            func.coalesce(span_table.c.attributes, func.JSON_OBJECT()),
                            reason_path,
                            "process_restart",
                        ),
                    )
                )
        return {
            "traces": traces.rowcount or 0,
            "spans": spans.rowcount or 0,
        }

    async def list_traces(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        started_after: datetime | None = None,
        started_before: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        table = self.tables[TRACES_TABLE]
        statement = (
            select(table)
            .order_by(table.c.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if tenant_id is not None:
            statement = statement.where(table.c.tenant_id == tenant_id)
        if user_id is not None:
            statement = statement.where(table.c.user_id == user_id)
        if session_id is not None:
            statement = statement.where(table.c.session_id == session_id)
        if started_after is not None:
            statement = statement.where(table.c.started_at >= started_after)
        if started_before is not None:
            statement = statement.where(table.c.started_at < started_before)
        async with self._session_factory() as session:
            result = await session.execute(statement)
            return [dict(row) for row in result.mappings().all()]

    async def list_spans(self, trace_id: str) -> list[dict[str, Any]]:
        table = self.tables[SPANS_TABLE]
        statement = (
            select(table)
            .where(table.c.trace_id == trace_id)
            .order_by(table.c.sequence_no.asc(), table.c.started_at.asc())
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
            return [dict(row) for row in result.mappings().all()]

    async def get_span(self, span_id: str) -> dict[str, Any] | None:
        table = self.tables[SPANS_TABLE]
        async with self._session_factory() as session:
            result = await session.execute(select(table).where(table.c.span_id == span_id))
            row = result.mappings().first()
            return dict(row) if row is not None else None

    async def list_events(self, trace_id: str) -> list[dict[str, Any]]:
        table = self.tables[EVENTS_TABLE]
        statement = (
            select(table)
            .where(table.c.trace_id == trace_id)
            .order_by(
                table.c.record_version.asc(),
                table.c.sequence_no.asc(),
                table.c.occurred_at.asc(),
                table.c.event_id.asc(),
            )
        )
        async with self._session_factory() as session:
            result = await session.execute(statement)
            return [dict(row) for row in result.mappings().all()]

    async def aggregate_tokens(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        started_after: datetime,
        started_before: datetime,
    ) -> dict[str, Any]:
        """Personal overview aggregation over the Trace table (section 12.2).

        ``user_id=None`` aggregates the whole tenant; ``tenant_id=None``
        aggregates across all tenants (admin view, review P1-6).
        """
        table = self.tables[TRACES_TABLE]
        statement = (
            select(
                func.count(table.c.trace_id).label("trace_count"),
                func.coalesce(func.sum(table.c.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(table.c.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(table.c.cache_read_tokens), 0).label("cache_read_tokens"),
                func.coalesce(func.sum(table.c.error_count), 0).label("error_count"),
                func.count(func.distinct(func.date(table.c.started_at))).label("active_days"),
                func.count(func.distinct(table.c.session_id)).label("conversation_count"),
            )
            .where(table.c.started_at >= started_after)
            .where(table.c.started_at < started_before)
        )
        if tenant_id is not None:
            statement = statement.where(table.c.tenant_id == tenant_id)
        if user_id is not None:
            statement = statement.where(table.c.user_id == user_id)
        async with self._session_factory() as session:
            row = (await session.execute(statement)).mappings().first()
            return dict(row) if row is not None else {}

    async def daily_tokens(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        started_after: datetime,
        started_before: datetime,
    ) -> list[dict[str, Any]]:
        """Per-day token totals for the personal Calendar view."""
        table = self.tables[TRACES_TABLE]
        statement = (
            select(
                func.date(table.c.started_at).label("day"),
                func.count(table.c.trace_id).label("trace_count"),
                func.coalesce(func.sum(table.c.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(table.c.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(table.c.cache_read_tokens), 0).label("cache_read_tokens"),
            )
            .where(table.c.started_at >= started_after)
            .where(table.c.started_at < started_before)
            .group_by(func.date(table.c.started_at))
            .order_by(func.date(table.c.started_at).asc())
        )
        if tenant_id is not None:
            statement = statement.where(table.c.tenant_id == tenant_id)
        if user_id is not None:
            statement = statement.where(table.c.user_id == user_id)
        async with self._session_factory() as session:
            result = await session.execute(statement)
            return [dict(row) for row in result.mappings().all()]

    async def model_breakdown(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
        started_after: datetime,
        started_before: datetime,
    ) -> list[dict[str, Any]]:
        """Per-model token and call totals from finished Model Spans."""
        table = self.tables[SPANS_TABLE]
        statement = (
            select(
                table.c.model.label("model"),
                func.count(table.c.span_id).label("call_count"),
                func.coalesce(func.sum(table.c.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(table.c.output_tokens), 0).label("output_tokens"),
                func.coalesce(func.sum(table.c.cache_read_tokens), 0).label("cache_read_tokens"),
            )
            .where(table.c.kind == "model")
            .where(table.c.model.is_not(None))
            .where(table.c.started_at >= started_after)
            .where(table.c.started_at < started_before)
            .group_by(table.c.model)
            .order_by(func.count(table.c.span_id).desc())
        )
        if tenant_id is not None:
            statement = statement.where(table.c.tenant_id == tenant_id)
        if user_id is not None:
            statement = statement.where(table.c.user_id == user_id)
        async with self._session_factory() as session:
            result = await session.execute(statement)
            return [dict(row) for row in result.mappings().all()]

    async def delete_older_than(self, cutoff: datetime) -> dict[str, int]:
        """Retention cleanup (review round 3, P1): only terminal traces whose
        completion is older than ``cutoff`` are removed — approval_required
        traces are kept, and deleting by trace id (instead of per-table time
        columns) never leaves orphaned spans/events behind."""
        deleted: dict[str, int] = {}
        traces_table = self.tables[TRACES_TABLE]
        async with self._session_factory() as session:
            async with session.begin():
                result = await session.execute(
                    select(traces_table.c.trace_id).where(
                        traces_table.c.status.in_(("completed", "failed", "cancelled")),
                        (traces_table.c.completed_at < cutoff)
                        | (
                            (traces_table.c.completed_at.is_(None))
                            & (traces_table.c.started_at < cutoff)
                        ),
                    ).limit(5000)
                )
                trace_ids = [row[0] for row in result.all()]
                if not trace_ids:
                    return deleted
                for table_name, id_column in (
                    (EVENTS_TABLE, "trace_id"),
                    (SPANS_TABLE, "trace_id"),
                    (TRACES_TABLE, "trace_id"),
                ):
                    table = self.tables[table_name]
                    result = await session.execute(
                        sa_delete(table).where(getattr(table.c, id_column).in_(trace_ids))
                    )
                    deleted[table_name] = result.rowcount or 0
        return deleted

    async def close(self) -> None:
        await self._engine.dispose()
