"""Native chat model construction for chat, document-worker, health and tests.

Returns langchain_openai.ChatOpenAI / BaseChatModel instances directly.
No permanent model wrapper; max_retries stays 0 unless explicitly configured.
"""

from __future__ import annotations

from typing import Any

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.model_providers.models import ModelRuntimeConfig


def create_native_chat_model(
    *,
    model: str,
    api_key: str,
    base_url: str,
    timeout_seconds: float = 60.0,
    max_completion_tokens: int | None = None,
    max_retries: int = 0,
    http_client: httpx.AsyncClient | None = None,
    use_responses_api: bool = False,
    **kwargs: Any,
) -> ChatOpenAI:
    """Build a native ChatOpenAI. max_retries defaults to 0 to match legacy factory.

    Provider quirks preserved from the proven compatibility path: strip
    ``/chat/completions`` suffix from base_url and disable the Responses API.
    """
    if not base_url or not model:
        raise PlatformError(
            code="INVALID_REQUEST",
            message="The LLM provider is not configured",
            status_code=503,
            source="model",
            user_message="The language model is not configured for this service.",
        )
    params: dict[str, Any] = {
        "model": model,
        "api_key": SecretStr(api_key or "not-configured"),
        "base_url": _openai_base_url(base_url),
        "timeout": timeout_seconds,
        "max_retries": max_retries,
        "use_responses_api": use_responses_api,
    }
    if max_completion_tokens is not None:
        params["max_completion_tokens"] = max_completion_tokens
    if http_client is not None:
        params["http_async_client"] = http_client
    params.update(kwargs)
    return ChatOpenAI(**params)


def _openai_base_url(value: str) -> str:
    normalized = value.rstrip("/")
    suffix = "/chat/completions"
    return normalized[: -len(suffix)] if normalized.endswith(suffix) else normalized


def create_model_from_runtime(
    runtime: ModelRuntimeConfig,
    *,
    max_completion_tokens: int | None = None,
    max_retries: int = 0,
    http_client: httpx.AsyncClient | None = None,
) -> BaseChatModel:
    """Native model for an actor-scoped runtime config."""
    return create_native_chat_model(
        model=runtime.model,
        api_key=runtime.api_key,
        base_url=runtime.base_url,
        timeout_seconds=runtime.timeout_seconds,
        max_completion_tokens=max_completion_tokens,
        max_retries=max_retries,
        http_client=http_client,
    )


def create_model_from_settings(
    *,
    model: str,
    api_key: str | None,
    base_url: str | None,
    timeout_seconds: float = 60.0,
    max_completion_tokens: int | None = None,
    max_retries: int = 0,
    http_client: httpx.AsyncClient | None = None,
) -> BaseChatModel:
    """Native model from environment/default settings (document-worker, health)."""
    return create_native_chat_model(
        model=model,
        api_key=api_key or "",
        base_url=base_url or "",
        timeout_seconds=timeout_seconds,
        max_completion_tokens=max_completion_tokens,
        max_retries=max_retries,
        http_client=http_client,
    )
