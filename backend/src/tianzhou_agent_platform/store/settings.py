"""Storage settings models."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .errors import StorageError
from .routing import StorageRouteResolver
from .validation import (
    DEFAULT_MAX_PAGE_SIZE,
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_PAGE_SIZE,
    validate_namespace,
)


class _StorageBaseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MySQLAdapterSettings(_StorageBaseModel):
    type: Literal["mysql"] = "mysql"
    url: SecretStr
    table_name: str = "storage_resources"
    ssl_ca: str | None = None
    ssl_cert: str | None = None
    ssl_key: SecretStr | None = None


class RedisAdapterSettings(_StorageBaseModel):
    type: Literal["redis"] = "redis"
    url: SecretStr
    key_prefix: str = "tzap:storage"
    ssl: bool = False
    ssl_ca_certs: str | None = None
    ssl_certfile: str | None = None
    ssl_keyfile: SecretStr | None = None


class S3AdapterSettings(_StorageBaseModel):
    type: Literal["s3"] = "s3"
    bucket: str = Field(min_length=1)
    endpoint_url: str | None = None
    region_name: str | None = None
    access_key_id: SecretStr | None = None
    secret_access_key: SecretStr | None = None
    session_token: SecretStr | None = None
    use_ssl: bool = True
    verify_ssl: bool | str = True
    server_side_encryption: str | None = None
    kms_key_id: str | None = None


class NASAdapterSettings(_StorageBaseModel):
    type: Literal["nas"] = "nas"
    root_path: Path
    create_root: bool = True


AdapterSettings = Annotated[
    MySQLAdapterSettings | RedisAdapterSettings | S3AdapterSettings | NASAdapterSettings,
    Field(discriminator="type"),
]


class StorageSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="TZAP_STORAGE_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    adapters: dict[str, AdapterSettings]
    routes: dict[str, str] = Field(default_factory=dict)
    default_adapter: str | None = None
    default_timeout_seconds: float = Field(default=5.0, gt=0)
    max_payload_bytes: int = Field(default=DEFAULT_MAX_PAYLOAD_BYTES, ge=0)
    default_page_size: int = Field(default=DEFAULT_PAGE_SIZE, gt=0)
    max_page_size: int = Field(default=DEFAULT_MAX_PAGE_SIZE, gt=0)

    @field_validator("adapters")
    @classmethod
    def _validate_adapter_names(cls, adapters: dict[str, AdapterSettings]) -> dict[str, AdapterSettings]:
        if not adapters:
            raise ValueError("At least one storage adapter must be configured")
        for adapter_name in adapters:
            try:
                validate_namespace(adapter_name)
            except StorageError as exc:
                raise ValueError(str(exc)) from exc
        return adapters

    @field_validator("routes")
    @classmethod
    def _validate_route_names(cls, routes: dict[str, str]) -> dict[str, str]:
        for namespace in routes:
            try:
                validate_namespace(namespace)
            except StorageError as exc:
                raise ValueError(str(exc)) from exc
        return routes

    @model_validator(mode="after")
    def _validate_routes_and_limits(self) -> StorageSettings:
        if self.default_page_size > self.max_page_size:
            raise ValueError("default_page_size must not exceed max_page_size")
        try:
            StorageRouteResolver(
                adapter_names=self.adapters.keys(),
                routes=self.routes,
                default_adapter=self.default_adapter,
            )
        except StorageError as exc:
            raise ValueError(str(exc)) from exc
        return self

    def create_route_resolver(self) -> StorageRouteResolver:
        return StorageRouteResolver(
            adapter_names=self.adapters.keys(),
            routes=self.routes,
            default_adapter=self.default_adapter,
        )
