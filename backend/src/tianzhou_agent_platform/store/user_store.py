"""User accounts read and written in SQL.

Email and GitHub uniqueness are enforced by unique keys, so concurrent registrations on different replicas cannot
create duplicates; a violated key is reported with the same conflict error the in-memory repository raises.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.core.errors import PlatformError, conflict
from tianzhou_agent_platform.store.conversation_store import SessionSource
from tianzhou_agent_platform.store.memory_repository import (
    email_registered,
    github_already_linked,
    github_email_registered,
    new_github_user,
    normalized_email,
    relinked_github_user,
)
from tianzhou_agent_platform.store.repository_schema import model_from_mapping, table_values, users_table


class UserStore:
    def __init__(self, database: SessionSource) -> None:
        self._database = database

    def _session(self) -> AsyncSession:
        return self._database.session_factory()

    async def create(self, user: UserRecord) -> None:
        try:
            async with self._session() as session, session.begin():
                await session.execute(insert(users_table).values(_user_row(user)))
        except IntegrityError:
            raise await self._conflict(user) from None

    async def get(self, user_id: str) -> UserRecord | None:
        return await self._find(users_table.c.id == user_id)

    async def find_by_email(self, email: str) -> UserRecord | None:
        return await self._find(users_table.c.email_normalized == normalized_email(email))

    async def list_users(self, *, query: str | None, limit: int, offset: int) -> list[UserRecord]:
        statement = select(users_table)
        normalized = (query or "").strip().lower()
        if normalized:
            statement = statement.where(
                or_(
                    *(
                        func.lower(column).contains(normalized, autoescape=True)
                        for column in (
                            users_table.c.id,
                            users_table.c.name,
                            users_table.c.email,
                            users_table.c.tenant_id,
                        )
                    )
                )
            )
        statement = (
            statement.order_by(users_table.c.created_at.desc(), users_table.c.id.desc())
            .limit(max(1, limit))
            .offset(max(0, offset))
        )
        async with self._session() as session:
            rows = (await session.execute(statement)).mappings().all()
        return [model_from_mapping(UserRecord, row) for row in rows]

    async def upsert_github(
        self,
        *,
        github_id: str,
        github_login: str,
        email: str,
        name: str,
        avatar_url: str | None,
    ) -> UserRecord:
        email = normalized_email(email)
        linked = users_table.c.github_id == github_id
        # A second attempt covers a concurrent first sign-in of the same GitHub account: its insert loses on the
        # unique key, and the retry finds and updates the row the winner created.
        for attempt in range(2):
            try:
                async with self._session() as session, session.begin():
                    # Lock only a row known to exist. A locking read that finds nothing takes an InnoDB gap lock,
                    # and two first sign-ins holding the same gap deadlock when both insert into it.
                    if await self._find_github(github_id) is None:
                        user = new_github_user(
                            github_id=github_id,
                            github_login=github_login,
                            email=email,
                            name=name,
                            avatar_url=avatar_url,
                        )
                        await session.execute(insert(users_table).values(_user_row(user)))
                    else:
                        row = (await session.execute(select(users_table).where(linked).with_for_update())).mappings()
                        user = relinked_github_user(
                            model_from_mapping(UserRecord, row.one()),
                            github_login=github_login,
                            name=name,
                            avatar_url=avatar_url,
                        )
                        await session.execute(
                            update(users_table).where(users_table.c.id == user.id).values(_user_row(user))
                        )
                return user
            except IntegrityError:
                if attempt == 0 and await self._find_github(github_id) is not None:
                    continue
                if await self.find_by_email(email) is not None:
                    raise github_email_registered() from None
                raise conflict("GitHub sign-in is already in progress. Retry the request.") from None
        raise AssertionError("unreachable")

    async def _find(self, condition: ColumnElement[bool]) -> UserRecord | None:
        async with self._session() as session:
            row = (await session.execute(select(users_table).where(condition))).mappings().first()
        return None if row is None else model_from_mapping(UserRecord, row)

    async def _find_github(self, github_id: str) -> UserRecord | None:
        return await self._find(users_table.c.github_id == github_id)

    async def _conflict(self, user: UserRecord) -> PlatformError:
        """The error for a rejected insert, after finding which unique key it violated."""
        if await self.find_by_email(str(user.email)) is not None:
            return email_registered()
        if user.github_id and await self._find_github(user.github_id) is not None:
            return github_already_linked()
        return conflict("This user already exists")


def _user_row(user: UserRecord) -> dict[str, Any]:
    return table_values(
        users_table,
        user,
        email_normalized=normalized_email(str(user.email)),
        github_id=user.github_id or None,
    )
