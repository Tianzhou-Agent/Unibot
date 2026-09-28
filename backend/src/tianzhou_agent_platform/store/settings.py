from __future__ import annotations

from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TZ_STORAGE_",
        env_file=(str(_BACKEND_ROOT / ".env"), str(_BACKEND_ROOT / ".venv")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mysql_dsn: SecretStr = SecretStr("mysql+aiomysql://unibot:unibot@127.0.0.1:13306/unibot")
    mysql_pool_size: int = Field(default=5, gt=0)
    mysql_max_overflow: int = Field(default=10, ge=0)
    mysql_timeout_seconds: float = Field(default=5.0, gt=0)

    redis_dsn: SecretStr = SecretStr("redis://127.0.0.1:16379/0")
    obs_redis_dsn: SecretStr | None = None
    redis_timeout_seconds: float = Field(default=2.0, gt=0)
    redis_default_ttl_seconds: int | None = Field(default=None, gt=0)

    nas_root_path: Path = _REPOSITORY_ROOT / "data" / "nas"
    nas_max_file_size_bytes: int = Field(default=100 * 1024 * 1024, gt=0)

    @field_validator("nas_root_path")
    @classmethod
    def validate_nas_root_path(cls, value: Path) -> Path:
        if not str(value).strip():
            raise ValueError("NAS root path must not be empty")
        return value

    @field_validator("obs_redis_dsn", mode="before")
    @classmethod
    def empty_obs_redis_dsn_uses_default(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value
