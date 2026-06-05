from __future__ import annotations

from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

StoreConditionOperator = Literal["eq", "ne", "gt", "ge", "lt", "le"]


class DeleteResult(BaseModel):
    deleted: bool


class WriteResult(BaseModel):
    written: bool


class StoreRecord(BaseModel):
    resource: str
    id: str | int
    values: dict[str, Any]


class StoreCondition(BaseModel):
    field: str
    op: StoreConditionOperator
    value: Any

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str) -> str:
        field = value.strip()
        if not field:
            raise ValueError("Store condition field must not be empty")
        return field

    @field_validator("value")
    @classmethod
    def validate_value(cls, value: Any) -> Any:
        if value is None:
            raise ValueError("Store condition value must not be null")
        return value


class StoreQuery(BaseModel):
    filters: dict[str, Any] = Field(default_factory=dict)
    conditions: list[StoreCondition] = Field(default_factory=list)
    limit: int = Field(default=100, gt=0, le=1000)
    offset: int = Field(default=0, ge=0)


class StorePage(BaseModel):
    items: list[StoreRecord]
    limit: int
    offset: int


class CacheEntry(BaseModel):
    namespace: str
    key: str
    value: Any
    ttl_seconds: int | None = None


class StoragePath(BaseModel):
    relative_path: str

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        normalized = value.replace("\\", "/").strip()
        if not normalized:
            raise ValueError("Storage path must not be empty")
        if PurePosixPath(normalized).is_absolute() or PureWindowsPath(value).is_absolute():
            raise ValueError("Storage path must be relative")
        if any(part == ".." for part in PurePosixPath(normalized).parts):
            raise ValueError("Storage path must not contain parent traversal")
        return normalized


class FileMetadata(BaseModel):
    path: StoragePath
    size_bytes: int
    modified_at: datetime | None = None
    content_type: str | None = None
