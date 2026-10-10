"""Native chat model construction for chat, document-worker, health and tests.

Returns langchain_openai.ChatOpenAI / BaseChatModel instances directly.
No permanent model wrapper; max_retries stays 0 unless explicitly configured.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import httpx
import openai
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.model_providers.models import ModelRuntimeConfig


REASONING_KEY = "reasoning_content"
# Wall-clock limit of one model call. ``timeout`` only bounds the gap between bytes, so a stream that keeps
# trickling (provider keep-alives, a stuck proxy) would otherwise never end.
CALL_DEADLINE_SECONDS = 180.0
# Providers occasionally accept a request and never answer it; the same request then succeeds at once. A call that
# timed out before producing anything has shown the user nothing, so it is sent again this many times.
SILENT_TIMEOUT_RETRIES = 1


class UnibotChatOpenAI(ChatOpenAI):
    """ChatOpenAI with a wall-clock deadline per call that keeps the provider's ``reasoning_content``.

    The base class drops this non-standard field (e.g. MiMo, DeepSeek, Qwen thinking mode); here it lands in
    ``additional_kwargs["reasoning_content"]`` so the chat stream can show it. It is never sent back to the provider.
    A call past ``call_deadline_seconds`` fails with ``openai.APITimeoutError``, the provider's own timeout error;
    a timeout before the first chunk is retried ``silent_timeout_retries`` times.
    """

    call_deadline_seconds: float = CALL_DEADLINE_SECONDS
    silent_timeout_retries: int = SILENT_TIMEOUT_RETRIES

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        for attempt in range(self.silent_timeout_retries + 1):
            received = False
            try:
                async for chunk in self._astream_with_deadline(*args, **kwargs):
                    received = True
                    yield chunk
                return
            except openai.APITimeoutError:
                if received or attempt == self.silent_timeout_retries:
                    raise

    async def _astream_with_deadline(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.call_deadline_seconds
        stream = super()._astream(*args, **kwargs)
        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(anext(stream), max(deadline - loop.time(), 0))
                except StopAsyncIteration:
                    return
                except TimeoutError as exc:
                    raise self._deadline_error() from exc
                yield chunk
        finally:
            await stream.aclose()

    async def _agenerate(self, *args: Any, **kwargs: Any) -> ChatResult:
        for attempt in range(self.silent_timeout_retries + 1):
            try:
                return await asyncio.wait_for(super()._agenerate(*args, **kwargs), self.call_deadline_seconds)
            except openai.APITimeoutError:
                if attempt == self.silent_timeout_retries:
                    raise
            except TimeoutError as exc:
                if attempt == self.silent_timeout_retries:
                    raise self._deadline_error() from exc
        raise AssertionError("unreachable")

    def _deadline_error(self) -> openai.APITimeoutError:
        return openai.APITimeoutError(
            request=httpx.Request("POST", f"{str(self.openai_api_base or '').rstrip('/')}/chat/completions")
        )

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ) -> ChatGenerationChunk | None:
        generation = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)
        choices = chunk.get("choices") or chunk.get("chunk", {}).get("choices") or []
        if generation is not None and choices:
            reasoning = (choices[0].get("delta") or {}).get(REASONING_KEY)
            if isinstance(reasoning, str) and reasoning:
                generation.message.additional_kwargs[REASONING_KEY] = reasoning
        return generation

    def _create_chat_result(self, response: Any, generation_info: dict | None = None) -> ChatResult:
        result = super()._create_chat_result(response, generation_info)
        response_dict = response if isinstance(response, dict) else response.model_dump()
        for generation, choice in zip(result.generations, response_dict.get("choices") or [], strict=False):
            reasoning = (choice.get("message") or {}).get(REASONING_KEY)
            if isinstance(reasoning, str) and reasoning:
                generation.message.additional_kwargs[REASONING_KEY] = reasoning
        return result


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
    """Build a native ChatOpenAI. ``max_retries`` defaults to 0: retries belong to the caller (no nested attempts).

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
        # Streamed agent turns must still report token usage.
        "stream_usage": True,
    }
    if max_completion_tokens is not None:
        params["max_completion_tokens"] = max_completion_tokens
    if http_client is not None:
        params["http_async_client"] = http_client
    params.update(kwargs)
    return UnibotChatOpenAI(**params)


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


def describe_model(model: BaseChatModel) -> tuple[str, str]:
    """(model name, chat completions endpoint) of a chat model, for model-call records."""
    name = getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__
    base_url = getattr(model, "openai_api_base", None) or ""
    return str(name), f"{str(base_url).rstrip('/')}/chat/completions"
