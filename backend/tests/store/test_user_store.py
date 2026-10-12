"""User accounts in SQL, shared by several repository instances (replicas).

Runs on SQLite; concurrent registration races are covered by ``test_user_store_mysql.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.repository import PersistentRepository
from tests.support.sqlite_store import sqlite_session_factory


class _SqlOnly:
    def __init__(self) -> None:
        self.session_factory = sqlite_session_factory()


def _replicas() -> tuple[PersistentRepository, PersistentRepository]:
    stores = StorageStores(mysql=_SqlOnly(), redis=None, nas=None)  # type: ignore[arg-type]
    return PersistentRepository(stores), PersistentRepository(stores)


def _user(user_id: str, email: str, **fields: object) -> UserRecord:
    return UserRecord(id=user_id, email=email, name=user_id, password_hash="hash", **fields)  # type: ignore[arg-type]


async def test_registration_is_visible_to_the_other_replica_and_email_is_case_insensitive() -> None:
    first, second = _replicas()
    await first.create_user(_user("u1", "Alice@Example.com"))

    assert (await second.find_user_by_id("u1")) is not None
    found = await second.find_user_by_email("  alice@EXAMPLE.com ")
    assert found is not None and found.id == "u1"
    assert await second.find_user_by_email("bob@example.com") is None

    with pytest.raises(PlatformError) as error:
        await second.create_user(_user("u2", "ALICE@example.com"))
    assert error.value.code == "CONFLICT"
    assert error.value.user_message == "该邮箱已注册。"


async def test_a_github_account_links_to_one_user_and_unlinked_users_do_not_collide() -> None:
    first, second = _replicas()
    await first.create_user(_user("u1", "a@example.com", github_id="42"))
    await first.create_user(_user("u2", "b@example.com", github_id=""))
    await second.create_user(_user("u3", "c@example.com", github_id=""))

    with pytest.raises(PlatformError) as error:
        await second.create_user(_user("u4", "d@example.com", github_id="42"))
    assert error.value.user_message == "该 GitHub 账号已绑定其他用户。"


async def test_github_sign_in_creates_then_updates_the_same_user() -> None:
    first, second = _replicas()
    created = await first.upsert_github_user(
        github_id="7", github_login="octo", email=" Octo@Example.com", name="Octo", avatar_url=None
    )
    updated = await second.upsert_github_user(
        github_id="7", github_login="octo-new", email="octo@example.com", name="", avatar_url="https://a/b.png"
    )

    assert updated.id == created.id
    assert str(created.email) == "octo@example.com"
    assert (updated.github_login, updated.name, updated.avatar_url) == ("octo-new", "Octo", "https://a/b.png")
    assert (await first.find_user_by_id(created.id)) == updated


async def test_github_sign_in_does_not_take_over_a_local_account() -> None:
    first, second = _replicas()
    await first.create_user(_user("u1", "octo@example.com"))

    with pytest.raises(PlatformError) as error:
        await second.upsert_github_user(
            github_id="7", github_login="octo", email="OCTO@example.com", name="Octo", avatar_url=None
        )
    assert error.value.user_message == "该邮箱已有账户，请先使用邮箱登录。"


async def test_list_searches_and_pages_newest_first() -> None:
    first, second = _replicas()
    start = datetime(2026, 10, 1, tzinfo=UTC)
    for index, (name, email) in enumerate(
        [("Ann", "ann@corp.example"), ("Ben", "ben@home.example"), ("Cat", "cat@corp.example")]
    ):
        await first.create_user(
            UserRecord(id=f"u{index}", email=email, name=name, created_at=start + timedelta(days=index))
        )

    assert [user.id for user in await second.list_users()] == ["u2", "u1", "u0"]
    assert [user.id for user in await second.list_users(query="CORP")] == ["u2", "u0"]
    assert [user.id for user in await second.list_users(query="ben")] == ["u1"]
    assert [user.id for user in await second.list_users(limit=1, offset=1)] == ["u1"]
    assert await second.list_users(query="%") == []
