"""Persistence contracts consumed by AuthService, OidcAuthService and the developer-token routes."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tianzhou_agent_platform.auth.models import DeveloperToken, OidcProvider, UserRecord


@runtime_checkable
class UserRepository(Protocol):
    async def create_user(self, user: UserRecord) -> UserRecord: ...

    async def find_user_by_id(self, user_id: str) -> UserRecord | None: ...

    async def find_user_by_email(self, email: str) -> UserRecord | None: ...

    async def list_users(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[UserRecord]: ...

    async def upsert_github_user(
        self,
        *,
        github_id: str,
        github_login: str,
        email: str,
        name: str,
        avatar_url: str | None,
    ) -> UserRecord: ...

    async def upsert_huawei_user(self, *, huawei_id: str, email: str, name: str) -> UserRecord: ...

    async def upsert_oidc_user(
        self, *, provider: OidcProvider, identity: str, email: str, name: str | None, avatar_url: str | None
    ) -> UserRecord: ...


@runtime_checkable
class DeveloperTokenRepository(Protocol):
    async def create_developer_token(self, token: DeveloperToken) -> DeveloperToken: ...

    async def list_developer_tokens(self, *, tenant_id: str, user_id: str) -> list[DeveloperToken]: ...

    async def find_developer_token(self, token_hash: str) -> DeveloperToken | None: ...

    async def touch_developer_token(self, token: DeveloperToken) -> None: ...

    async def delete_developer_token(self, token_id: str, *, tenant_id: str, user_id: str) -> bool: ...
