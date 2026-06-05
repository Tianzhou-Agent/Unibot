from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sqlalchemy import Table, delete as sa_delete, insert, select, update as sa_update
from sqlalchemy.exc import DataError, IntegrityError, OperationalError, SQLAlchemyError, TimeoutError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.schema import MetaData
from sqlalchemy.sql.sqltypes import String

from tianzhou_agent_platform.store.errors import (
    StorageBackendUnavailableError,
    StorageError,
    StorageNotFoundError,
    StorageTimeoutError,
    StorageUnknownBackendError,
    StorageUnsupportedCapabilityError,
    StorageValidationError,
)
from tianzhou_agent_platform.store.models import DeleteResult, StorePage, StoreQuery, StoreRecord


class MySqlStore:
    def __init__(
        self,
        engine: AsyncEngine,
        session_factory: async_sessionmaker[Any],
        resource_tables: Mapping[str, Table] | None = None,
        id_column: str = "id",
    ) -> None:
        self._engine = engine
        self._session_factory = session_factory
        self._resource_tables = dict(resource_tables or {})
        self._id_column = id_column

    @classmethod
    def from_dsn(
        cls,
        dsn: str,
        *,
        pool_size: int = 5,
        max_overflow: int = 10,
        resource_tables: Mapping[str, Table] | None = None,
        id_column: str = "id",
    ) -> "MySqlStore":
        engine = create_async_engine(dsn, pool_size=pool_size, max_overflow=max_overflow)
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        return cls(engine, session_factory, resource_tables=resource_tables, id_column=id_column)

    async def create(self, resource: str, values: dict[str, Any]) -> StoreRecord:
        table = self._table_for(resource)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    result = await session.execute(insert(table).values(**values))
                    record_id = values.get(self._id_column)
                    if record_id is None and result.inserted_primary_key:
                        record_id = result.inserted_primary_key[0]
                    if record_id is None:
                        raise StorageUnknownBackendError("MySQL insert did not return a record id")
                    row = await self._read_row(session, table, record_id)
                    return self._record_from_row(resource, record_id, row or values)
        except StorageError:
            raise
        except SQLAlchemyError as exc:
            raise self._map_sqlalchemy_error(exc) from exc

    async def read(self, resource: str, record_id: str | int) -> StoreRecord | None:
        table = self._table_for(resource)
        try:
            async with self._session_factory() as session:
                row = await self._read_row(session, table, record_id)
                if row is None:
                    return None
                return self._record_from_row(resource, record_id, row)
        except SQLAlchemyError as exc:
            raise self._map_sqlalchemy_error(exc) from exc

    async def update(self, resource: str, record_id: str | int, values: dict[str, Any]) -> StoreRecord:
        table = self._table_for(resource)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    await session.execute(
                        sa_update(table).where(table.c[self._id_column] == record_id).values(**values)
                    )
                    row = await self._read_row(session, table, record_id)
                    if row is None:
                        raise StorageNotFoundError(f"MySQL resource '{resource}' record was not found")
                    return self._record_from_row(resource, record_id, row)
        except StorageError:
            raise
        except SQLAlchemyError as exc:
            raise self._map_sqlalchemy_error(exc) from exc

    async def delete(self, resource: str, record_id: str | int) -> DeleteResult:
        table = self._table_for(resource)
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    result = await session.execute(sa_delete(table).where(table.c[self._id_column] == record_id))
                    return DeleteResult(deleted=(result.rowcount or 0) > 0)
        except SQLAlchemyError as exc:
            raise self._map_sqlalchemy_error(exc) from exc

    async def query(self, resource: str, query: StoreQuery) -> StorePage:
        table = self._table_for(resource)
        statement = select(table).limit(query.limit).offset(query.offset)
        for field, value in query.filters.items():
            if field not in table.c:
                raise StorageValidationError(f"Unknown filter field '{field}' for MySQL resource '{resource}'")
            statement = statement.where(table.c[field] == value)
        for field, value in query.contains_filters.items():
            if field not in table.c:
                raise StorageValidationError(f"Unknown contains filter field '{field}' for MySQL resource '{resource}'")
            column = table.c[field]
            if not isinstance(column.type, String):
                raise StorageValidationError(
                    f"Contains filter field '{field}' for MySQL resource '{resource}' must be a string column"
                )
            statement = statement.where(column.contains(value))

        try:
            async with self._session_factory() as session:
                result = await session.execute(statement)
                items = [
                    self._record_from_row(resource, row[self._id_column], dict(row))
                    for row in result.mappings().all()
                ]
                return StorePage(items=items, limit=query.limit, offset=query.offset)
        except SQLAlchemyError as exc:
            raise self._map_sqlalchemy_error(exc) from exc

    async def close(self) -> None:
        await self._engine.dispose()

    async def create_tables(self, metadata: MetaData) -> None:
        try:
            async with self._engine.begin() as connection:
                await connection.run_sync(metadata.create_all)
        except SQLAlchemyError as exc:
            raise self._map_sqlalchemy_error(exc) from exc

    def _table_for(self, resource: str) -> Table:
        table = self._resource_tables.get(resource)
        if table is None:
            raise StorageUnsupportedCapabilityError(f"MySQL resource '{resource}' is not configured")
        if self._id_column not in table.c:
            raise StorageValidationError(f"MySQL resource '{resource}' does not expose id column '{self._id_column}'")
        return table

    async def _read_row(self, session: Any, table: Table, record_id: str | int) -> dict[str, Any] | None:
        result = await session.execute(select(table).where(table.c[self._id_column] == record_id))
        row = result.mappings().first()
        return dict(row) if row is not None else None

    def _record_from_row(self, resource: str, record_id: str | int, row: Mapping[str, Any]) -> StoreRecord:
        return StoreRecord(resource=resource, id=record_id, values=dict(row))

    def _map_sqlalchemy_error(self, exc: SQLAlchemyError) -> StorageError:
        if isinstance(exc, TimeoutError):
            return StorageTimeoutError("MySQL operation timed out")
        if isinstance(exc, OperationalError):
            return StorageBackendUnavailableError("MySQL backend is unavailable")
        if isinstance(exc, (DataError, IntegrityError)):
            return StorageValidationError("MySQL rejected the requested data")
        return StorageUnknownBackendError("MySQL operation failed")
