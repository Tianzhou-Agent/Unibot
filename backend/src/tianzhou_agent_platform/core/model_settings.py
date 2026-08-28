from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from typing import Iterator, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator

from tianzhou_agent_platform.core.base import StrictModel, utc_now

ProviderType = Literal["openai", "deepseek", "openrouter", "ollama", "custom"]
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
MIN_CONTEXT_WINDOW_TOKENS = 4_096
MAX_CONTEXT_WINDOW_TOKENS = 10_000_000


class ModelDefinitionInput(StrictModel):
    id: str | None = None
    name: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    enabled: bool = True
    context_window_tokens: int = Field(
        default=DEFAULT_CONTEXT_WINDOW_TOKENS,
        ge=MIN_CONTEXT_WINDOW_TOKENS,
        le=MAX_CONTEXT_WINDOW_TOKENS,
    )

    @field_validator("name", "model")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class ModelProviderCreate(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"
    provider_type: ProviderType
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = Field(default="", max_length=1000)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    models: list[ModelDefinitionInput] = Field(min_length=1, max_length=50)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        return value.strip()

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Provider base URL must be an HTTP or HTTPS URL")
        return normalized

    @model_validator(mode="after")
    def validate_models(self) -> "ModelProviderCreate":
        model_ids = [item.model.casefold() for item in self.models]
        if len(model_ids) != len(set(model_ids)):
            raise ValueError("Provider model identifiers must be unique")
        return self


class ModelProviderUpdate(ModelProviderCreate):
    api_key: str | None = Field(default=None, max_length=1000)


class ModelDiscoveryRequest(StrictModel):
    provider_id: str | None = None
    user_id: str = "anonymous"
    tenant_id: str = "default"
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str | None = Field(default=None, max_length=1000)
    timeout_seconds: float = Field(default=60.0, gt=0, le=600)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Provider base URL must be an HTTP or HTTPS URL")
        return normalized


class DiscoveredModel(StrictModel):
    id: str
    name: str
    context_window_tokens: int | None = Field(
        default=None,
        ge=MIN_CONTEXT_WINDOW_TOKENS,
        le=MAX_CONTEXT_WINDOW_TOKENS,
    )


class ModelDiscoveryResponse(StrictModel):
    models: list[DiscoveredModel]


class ModelDefinition(StrictModel):
    id: str
    name: str
    model: str
    enabled: bool = True
    is_default: bool = False
    context_window_tokens: int = Field(
        default=DEFAULT_CONTEXT_WINDOW_TOKENS,
        ge=MIN_CONTEXT_WINDOW_TOKENS,
        le=MAX_CONTEXT_WINDOW_TOKENS,
    )


class ModelProviderRecord(StrictModel):
    id: str
    user_id: str
    tenant_id: str
    provider_type: ProviderType
    name: str
    base_url: str
    api_key: str = Field(default="", repr=False)
    timeout_seconds: float
    models: list[ModelDefinition]
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ModelProviderView(StrictModel):
    id: str
    provider_type: ProviderType
    name: str
    base_url: str
    api_key_masked: str
    has_api_key: bool
    timeout_seconds: float
    models: list[ModelDefinition]


class ActiveModel(StrictModel):
    source: Literal["user", "environment", "unconfigured"]
    provider_id: str | None = None
    provider_name: str | None = None
    model_id: str | None = None
    model_name: str | None = None
    model: str | None = None


class ModelSettingsResponse(StrictModel):
    providers: list[ModelProviderView]
    active_model: ActiveModel


class ModelActor(StrictModel):
    user_id: str = "anonymous"
    tenant_id: str = "default"


class ModelHealthResult(StrictModel):
    status: Literal["healthy", "unhealthy"]
    checked_at: datetime = Field(default_factory=utc_now)
    latency_ms: float
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ModelRuntimeConfig:
    provider_id: str
    provider_name: str
    base_url: str
    api_key: str
    model_id: str
    model_name: str
    model: str
    context_window_tokens: int
    timeout_seconds: float

    @property
    def chat_completions_url(self) -> str:
        return chat_completions_url(self.base_url)


_current_model_runtime: ContextVar[ModelRuntimeConfig | None] = ContextVar(
    "current_model_runtime",
    default=None,
)


def current_model_runtime() -> ModelRuntimeConfig | None:
    return _current_model_runtime.get()


def current_context_window_tokens(default: int) -> int:
    runtime = current_model_runtime()
    return runtime.context_window_tokens if runtime is not None else default


@contextmanager
def use_model_runtime(config: ModelRuntimeConfig | None) -> Iterator[None]:
    token = _current_model_runtime.set(config)
    try:
        yield
    finally:
        _current_model_runtime.reset(token)


def chat_completions_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def models_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/chat/completions"):
        normalized = normalized.removesuffix("/chat/completions")
    if normalized.endswith("/models"):
        return normalized
    return f"{normalized}/models"


def mask_api_key(api_key: str) -> str:
    if not api_key:
        return "未配置"
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:3]}{'*' * 6}{api_key[-4:]}"


def provider_view(record: ModelProviderRecord) -> ModelProviderView:
    return ModelProviderView(
        id=record.id,
        provider_type=record.provider_type,
        name=record.name,
        base_url=record.base_url,
        api_key_masked=mask_api_key(record.api_key),
        has_api_key=bool(record.api_key),
        timeout_seconds=record.timeout_seconds,
        models=record.models,
    )
