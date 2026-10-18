from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import EmailStr, Field, field_validator

from tianzhou_agent_platform.core.base import StrictModel, utc_now

OidcProvider = Literal["google", "microsoft", "apple"]
AuthProvider = Literal["password", "github", "huawei", "google", "microsoft", "apple"]


class UserRecord(StrictModel):
    id: str
    email: EmailStr
    name: str = Field(min_length=1, max_length=80)
    password_hash: str | None = None
    github_id: str | None = None
    github_login: str | None = None
    huawei_id: str | None = None
    oidc_identities: dict[OidcProvider, str] = Field(default_factory=dict)
    avatar_url: str | None = None
    tenant_id: str = Field(default="default", min_length=1, max_length=160)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("oidc_identities", mode="before")
    @classmethod
    def normalize_oidc_identities(cls, value: object) -> object:
        return {} if value is None else value


class UserView(StrictModel):
    id: str
    email: EmailStr
    name: str
    avatar_url: str | None = None
    tenant_id: str
    providers: list[AuthProvider]
    is_admin: bool = False

    @classmethod
    def from_record(cls, user: UserRecord, *, is_admin: bool = False) -> "UserView":
        providers: list[AuthProvider] = []
        if user.password_hash:
            providers.append("password")
        if user.github_id:
            providers.append("github")
        if user.huawei_id:
            providers.append("huawei")
        providers.extend(user.oidc_identities)
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
    default_language: Literal["en", "zh"]
    registration_enabled: bool
    github_enabled: bool
    huawei_enabled: bool
    google_enabled: bool
    microsoft_enabled: bool
    apple_enabled: bool


class DeveloperToken(StrictModel):
    """A credential for the ``aina`` CLI: it can only validate, publish and refresh AINA packages (design §12)."""

    id: str
    user_id: str
    tenant_id: str
    name: str = Field(min_length=1, max_length=80)
    # sha256 of the token; the token itself is shown once, when it is created.
    token_hash: str = Field(exclude=True)
    prefix: str
    created_at: datetime = Field(default_factory=utc_now)
    expires_at: datetime
    last_used_at: datetime | None = None


class DeveloperTokenCreate(StrictModel):
    name: str = Field(min_length=1, max_length=80)
    expires_in_days: int = Field(default=90, ge=1, le=365)


class CreatedDeveloperToken(StrictModel):
    token: str
    record: DeveloperToken
