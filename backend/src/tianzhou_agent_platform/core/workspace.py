from datetime import datetime

from pydantic import Field, field_validator

from tianzhou_agent_platform.core.base import StrictModel, utc_now


class WorkspaceCreate(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Workspace name must not be empty")
        return normalized


class WorkspaceUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2_000)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Workspace name must not be empty")
        return normalized


class Workspace(StrictModel):
    id: str
    user_id: str
    tenant_id: str
    name: str
    description: str = ""
    storage_key: str = Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9_-]+$")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
