"""Shared plumbing for the repository's SQL stores."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel
from sqlalchemy import ColumnElement, Select, Table, delete, insert, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tianzhou_agent_platform.store.repository_schema import model_from_mapping


class SessionSource(Protocol):
    @property
    def session_factory(self) -> async_sessionmaker[AsyncSession]: ...


class SqlStore:
    def __init__(self, database: SessionSource) -> None:
        self._database = database

    def _session(self) -> AsyncSession:
        return self._database.session_factory()

    async def _one[ModelT: BaseModel](self, model: type[ModelT], statement: Select[Any]) -> ModelT | None:
        async with self._session() as session:
            row = (await session.execute(statement)).mappings().first()
        return None if row is None else model_from_mapping(model, row)

    async def _all[ModelT: BaseModel](self, model: type[ModelT], statement: Select[Any]) -> list[ModelT]:
        async with self._session() as session:
            rows = (await session.execute(statement)).mappings().all()
        return [model_from_mapping(model, row) for row in rows]

    async def _put(self, table: Table, row: dict[str, Any]) -> None:
        """Insert ``row``, or replace the row with the same id.

        Inserting first, rather than a locking read to decide, avoids InnoDB gap locks on missing rows.
        """
        try:
            async with self._session() as session, session.begin():
                await session.execute(insert(table).values(row))
        except IntegrityError:
            async with self._session() as session, session.begin():
                result = await session.execute(update(table).where(table.c.id == row["id"]).values(row))
            if not result.rowcount:  # type: ignore[attr-defined]
                raise  # the insert broke a key other than the id, so there is no row to replace

    async def _delete(
        self, table: Table, condition: ColumnElement[bool], *dependents: tuple[Table, ColumnElement[bool]]
    ) -> int:
        """Delete matching rows and their ``dependents`` in one transaction; returns how many rows of ``table`` went."""
        async with self._session() as session, session.begin():
            result = await session.execute(delete(table).where(condition))
            for dependent, dependent_condition in dependents:
                await session.execute(delete(dependent).where(dependent_condition))
        return result.rowcount or 0  # type: ignore[attr-defined]


def matches(column: Any, value: Any) -> ColumnElement[bool]:
    """``column = value`` that also matches when both are NULL, as Python's ``==`` does for None."""
    return column.is_(None) if value is None else column == value  # type: ignore[no-any-return]
