from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TZ_STORAGE_", env_file=".env", extra="ignore")

    mysql_dsn: SecretStr
    mysql_pool_size: int = Field(default=5, gt=0)
    mysql_max_overflow: int = Field(default=10, ge=0)
    mysql_timeout_seconds: float = Field(default=5.0, gt=0)

    redis_dsn: SecretStr
    redis_timeout_seconds: float = Field(default=2.0, gt=0)
    redis_default_ttl_seconds: int | None = Field(default=None, gt=0)

    nas_root_path: Path
    nas_max_file_size_bytes: int = Field(default=100 * 1024 * 1024, gt=0)

    @field_validator("nas_root_path")
    @classmethod
    def validate_nas_root_path(cls, value: Path) -> Path:
        if not str(value).strip():
            raise ValueError("NAS root path must not be empty")
        return value
