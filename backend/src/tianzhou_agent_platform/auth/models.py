from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field, field_validator

from tianzhou_agent_platform.core.base import StrictModel, utc_now


class UserRecord(StrictModel):
    id: str
    email: EmailStr
    name: str = Field(min_length=1, max_length=80)
    password_hash: str | None = None
    github_id: str | None = None
    github_login: str | None = None
    avatar_url: str | None = None
    tenant_id: str = Field(default="default", min_length=1, max_length=160)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UserView(StrictModel):
    id: str
    email: EmailStr
    name: str
    avatar_url: str | None = None
    tenant_id: str
    providers: list[Literal["password", "github"]]
    is_admin: bool = False

    @classmethod
    def from_record(cls, user: UserRecord, *, is_admin: bool = False) -> "UserView":
        providers: list[Literal["password", "github"]] = []
        if user.password_hash:
            providers.append("password")
        if user.github_id:
            providers.append("github")
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            tenant_id=user.tenant_id,
            providers=providers,
            is_admin=is_admin,
        )


class AdminUserSummary(StrictModel):
    id: str
    email: EmailStr
    name: str
    avatar_url: str | None = None
    tenant_id: str
    created_at: datetime

    @classmethod
    def from_record(cls, user: UserRecord) -> "AdminUserSummary":
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            avatar_url=user.avatar_url,
            tenant_id=user.tenant_id,
            created_at=user.created_at,
        )


class RegisterRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    name: str = Field(min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name must not be empty")
        return normalized


class LoginRequest(StrictModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class AuthResponse(StrictModel):
    user: UserView


class AuthConfig(StrictModel):
    auth_required: bool
    registration_enabled: bool
    github_enabled: bool
