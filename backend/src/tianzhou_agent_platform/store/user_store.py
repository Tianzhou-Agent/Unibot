"""User accounts read and written in SQL.

Email, GitHub and Huawei uniqueness are enforced by unique keys, so concurrent registrations on different replicas cannot
create duplicates; a violated key is reported with the same conflict error the in-memory repository raises.
Google, Microsoft and Apple identities live in one JSON column without a key of their own; the account's email, which
is unique, stops a concurrent first sign-in from creating a second user.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import ColumnElement, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError

from tianzhou_agent_platform.auth.models import OidcProvider, UserRecord
from tianzhou_agent_platform.core.errors import PlatformError, conflict
from tianzhou_agent_platform.store.memory_repository import (
    email_registered,
    github_already_linked,
    github_email_registered,
    huawei_already_linked,
    linked_email_registered,
    new_github_user,
    new_huawei_user,
    new_oidc_user,
    normalized_email,
    oidc_already_linked,
    relinked_github_user,
    relinked_huawei_user,
    relinked_oidc_user,
)
from tianzhou_agent_platform.store.repository_schema import model_from_mapping, table_values, users_table
from tianzhou_agent_platform.store.sql import SqlStore


class UserStore(SqlStore):
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
        return await self._upsert_linked(
            users_table.c.github_id == github_id,
            create=lambda: new_github_user(
                github_id=github_id, github_login=github_login, email=email, name=name, avatar_url=avatar_url
            ),
            relink=lambda user: relinked_github_user(user, github_login=github_login, name=name, avatar_url=avatar_url),
            email=email,
            email_taken=github_email_registered,
            provider="GitHub",
        )

    async def upsert_huawei(self, *, huawei_id: str, email: str, name: str) -> UserRecord:
        email = normalized_email(email)
        return await self._upsert_linked(
            users_table.c.huawei_id == huawei_id,
            create=lambda: new_huawei_user(huawei_id=huawei_id, email=email, name=name),
            relink=lambda user: relinked_huawei_user(user, name=name),
            email=email,
            email_taken=lambda: linked_email_registered("Huawei"),
            provider="Huawei",
        )

    async def upsert_oidc(
        self, *, provider: OidcProvider, identity: str, email: str, name: str | None, avatar_url: str | None
    ) -> UserRecord:
        email = normalized_email(email)
        return await self._upsert_linked(
            _oidc_linked(provider, identity),
            create=lambda: new_oidc_user(
                provider=provider, identity=identity, email=email, name=name, avatar_url=avatar_url
            ),
            relink=lambda user: relinked_oidc_user(user, name=name, avatar_url=avatar_url),
            email=email,
            email_taken=lambda: linked_email_registered(provider),
            provider=provider,
        )

    async def _upsert_linked(
        self,
        linked: ColumnElement[bool],
        *,
        create: Callable[[], UserRecord],
        relink: Callable[[UserRecord], UserRecord],
        email: str,
        email_taken: Callable[[], PlatformError],
        provider: str,
    ) -> UserRecord:
        """Sign in through an external account: update the user it is linked to, or create one."""
        # A second attempt covers a concurrent first sign-in of the same account: its insert loses on a unique key
        # (the account id, or the email the account shares), and the retry finds and updates the winner's row.
        for attempt in range(2):
            try:
                async with self._session() as session, session.begin():
                    # Lock only a row known to exist. A locking read that finds nothing takes an InnoDB gap lock,
                    # and two first sign-ins holding the same gap deadlock when both insert into it.
                    if await self._find(linked) is None:
                        user = create()
                        await session.execute(insert(users_table).values(_user_row(user)))
                    else:
                        row = (await session.execute(select(users_table).where(linked).with_for_update())).mappings()
                        user = relink(model_from_mapping(UserRecord, row.one()))
                        await session.execute(
                            update(users_table).where(users_table.c.id == user.id).values(_user_row(user))
                        )
                return user
            except IntegrityError:
                if attempt == 0 and await self._find(linked) is not None:
                    continue
                if await self.find_by_email(email) is not None:
                    raise email_taken() from None
                raise conflict(f"{provider} sign-in is already in progress. Retry the request.") from None
        raise AssertionError("unreachable")

    async def _find(self, condition: ColumnElement[bool]) -> UserRecord | None:
        async with self._session() as session:
            row = (await session.execute(select(users_table).where(condition))).mappings().first()
        return None if row is None else model_from_mapping(UserRecord, row)

    async def _conflict(self, user: UserRecord) -> PlatformError:
        """The error for a rejected insert, after finding which unique key it violated."""
        if await self.find_by_email(str(user.email)) is not None:
            return email_registered()
        if user.github_id and await self._find(users_table.c.github_id == user.github_id) is not None:
            return github_already_linked()
        if user.huawei_id and await self._find(users_table.c.huawei_id == user.huawei_id) is not None:
            return huawei_already_linked()
        for provider, identity in user.oidc_identities.items():
            if await self._find(_oidc_linked(provider, identity)) is not None:
                return oidc_already_linked(provider)
        return conflict("This user already exists")


def _oidc_linked(provider: str, identity: str) -> ColumnElement[bool]:
    return users_table.c.oidc_identities[provider].as_string() == identity  # type: ignore[no-any-return]


def _user_row(user: UserRecord) -> dict[str, Any]:
    return table_values(
        users_table,
        user,
        email_normalized=normalized_email(str(user.email)),
        github_id=user.github_id or None,
        huawei_id=user.huawei_id or None,
    )
