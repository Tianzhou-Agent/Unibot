"""Native chat model construction for chat, document-worker, health and tests.

Returns langchain_openai.ChatOpenAI / BaseChatModel instances directly.
No permanent model wrapper; max_retries stays 0 unless explicitly configured.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Literal

import httpx
import openai
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.outputs import ChatGenerationChunk, ChatResult
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.model_providers.models import ModelRuntimeConfig


REASONING_KEY = "reasoning_content"
# Some providers (MiniMax M2/M3, models served by vLLM or Ollama) put the reasoning at the start of the reply text.
THINK_OPEN, THINK_CLOSE = "<think>", "</think>"
# Wall-clock limit of one model call. ``timeout`` only bounds the gap between bytes, so a stream that keeps
# trickling (provider keep-alives, a stuck proxy) would otherwise never end. A model allowed a long reply gets the
# time to write it at this slow pace.
CALL_DEADLINE_SECONDS = 180.0
SLOWEST_TOKENS_PER_SECOND = 40
# Providers occasionally accept a request and never answer it; the same request then succeeds at once. A call that
# timed out before producing anything has shown the user nothing, so it is sent again this many times.
SILENT_TIMEOUT_RETRIES = 1
# A rate limit (429), a provider error (5xx) or an unreachable provider is waited out and the call sent again after
# each of these delays, so a run that nobody is watching survives a busy minute instead of failing. Like a silent
# timeout, only a call that has produced nothing yet is sent again.
TRANSIENT_ERROR_DELAYS = (2.0, 4.0, 8.0, 16.0, 32.0)


class UnibotChatOpenAI(ChatOpenAI):
    """ChatOpenAI with a wall-clock deadline per call that keeps the provider's ``reasoning_content``.

    The base class drops this non-standard field (e.g. MiMo, DeepSeek, Qwen thinking mode); here it lands in
    ``additional_kwargs["reasoning_content"]`` so the chat stream can show it. A reply that starts with a
    ``<think>…</think>`` block (MiniMax) is split the same way. DeepSeek requires the reasoning to be replayed in
    subsequent requests that use tools, and MiniMax as a ``<think>`` block in the content; other providers keep
    their standard message format.
    A call past ``call_deadline_seconds`` fails with ``openai.APITimeoutError``, the provider's own timeout error;
    a timeout before the first chunk is retried ``silent_timeout_retries`` times, and a transient provider failure
    before the first chunk after each of ``transient_error_delays``.
    """

    call_deadline_seconds: float = CALL_DEADLINE_SECONDS
    silent_timeout_retries: int = SILENT_TIMEOUT_RETRIES
    transient_error_delays: tuple[float, ...] = TRANSIENT_ERROR_DELAYS

    def _get_request_payload(self, input_: Any, *, stop: list[str] | None = None, **kwargs: Any) -> dict:
        messages = self._convert_input(input_).to_messages()
        payload = super()._get_request_payload(messages, stop=stop, **kwargs)
        if not payload.get("stream"):
            # Nothing arrives before a non-streamed reply is complete, so its read timeout is the whole call's
            # time limit; the provider timeout stays the limit for connecting (and, when streaming, between chunks).
            connect = self.request_timeout if isinstance(self.request_timeout, (int, float)) else None
            payload["timeout"] = httpx.Timeout(self.deadline_seconds, connect=connect)
        replay = reasoning_replay(self.model_name)
        if "messages" in payload and replay is not None:
            for message, wire in zip(messages, payload["messages"], strict=True):
                if not isinstance(message, AIMessage):
                    continue
                reasoning = message.additional_kwargs.get(REASONING_KEY) or ""
                if replay == "field":
                    wire[REASONING_KEY] = reasoning
                elif reasoning:
                    wire["content"] = f"{THINK_OPEN}{reasoning}{THINK_CLOSE}\n\n{wire.get('content') or ''}"
        return payload

    async def _astream(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        failures: list[openai.OpenAIError] = []
        while True:
            received = False
            try:
                async for chunk in split_think_stream(self._astream_with_deadline(*args, **kwargs)):
                    received = True
                    yield chunk
                return
            except openai.OpenAIError as exc:
                delay = None if received else self._retry_delay(exc, failures)
                if delay is None:
                    raise
                failures.append(exc)
            await asyncio.sleep(delay)

    async def _astream_with_deadline(self, *args: Any, **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.deadline_seconds
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
        failures: list[openai.OpenAIError] = []
        while True:
            try:
                try:
                    return await asyncio.wait_for(super()._agenerate(*args, **kwargs), self.deadline_seconds)
                except TimeoutError as exc:
                    raise self._deadline_error() from exc
            except openai.OpenAIError as exc:
                delay = self._retry_delay(exc, failures)
                if delay is None:
                    raise
                failures.append(exc)
            await asyncio.sleep(delay)

    def _retry_delay(self, exc: openai.OpenAIError, failures: list[openai.OpenAIError]) -> float | None:
        """Seconds to wait before sending a call again that failed with ``exc`` after ``failures``; None to give up."""
        if isinstance(exc, openai.APITimeoutError):
            silent = sum(isinstance(failure, openai.APITimeoutError) for failure in failures)
            return 0.0 if silent < self.silent_timeout_retries else None
        transient = isinstance(exc, openai.APIConnectionError) or (
            isinstance(exc, openai.APIStatusError) and (exc.status_code == 429 or exc.status_code >= 500)
        )
        if not transient:
            return None
        waited = sum(not isinstance(failure, openai.APITimeoutError) for failure in failures)
        return self.transient_error_delays[waited] if waited < len(self.transient_error_delays) else None

    @property
    def deadline_seconds(self) -> float:
        return max(self.call_deadline_seconds, (self.max_tokens or 0) / SLOWEST_TOKENS_PER_SECOND)

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
            message = generation.message
            if isinstance(message.content, str):
                thought, text = split_think(message.content)
                if thought is not None:
                    message.content = text
                    generation.text = text
                    message.additional_kwargs[REASONING_KEY] = (
                        message.additional_kwargs.get(REASONING_KEY, "") + thought
                    )
        return result


def reasoning_replay(model_name: str) -> Literal["field", "think"] | None:
    """How a model wants its earlier reasoning back: as ``reasoning_content`` (DeepSeek, MiniMax-M3.1-Flash), as a
    ``<think>`` block in the content (other MiniMax models), or not at all."""
    model = model_name.lower()
    if model.startswith("deepseek") or ("minimax" in model and "m3.1-flash" in model):
        return "field"
    if "minimax" in model:
        return "think"
    return None


def split_think(content: str) -> tuple[str | None, str]:
    """(reasoning, reply) of a reply that starts with a ``<think>`` block; (None, content) otherwise."""
    stripped = content.lstrip()
    if not stripped.startswith(THINK_OPEN):
        return None, content
    thought, closed, rest = stripped[len(THINK_OPEN) :].partition(THINK_CLOSE)
    return thought.strip(), rest.lstrip() if closed else ""


async def split_think_stream(stream: AsyncIterator[ChatGenerationChunk]) -> AsyncIterator[ChatGenerationChunk]:
    """Move a leading ``<think>…</think>`` block of a streamed reply into ``reasoning_content``.

    Tags may be cut across chunks, so text that could still be the start of a tag is held back until it is known.
    """
    # start: not yet known; think: inside the block; after: the block closed, blank lines are dropped; reply: as is
    state = "start"
    pending = ""
    async for chunk in stream:
        text = chunk.message.content if isinstance(chunk.message.content, str) else ""
        if state == "reply" or not text:
            yield chunk
            continue
        pending += text
        reasoning, reply = "", ""
        if state == "start":
            stripped = pending.lstrip()
            if stripped.startswith(THINK_OPEN):
                state, pending = "think", stripped[len(THINK_OPEN) :]
            elif THINK_OPEN.startswith(stripped):
                yield _with_text(chunk, "", "")  # tool calls and usage still pass through
                continue
            else:
                state, reply, pending = "reply", pending, ""
        if state == "think":
            thought, closed, rest = pending.partition(THINK_CLOSE)
            if closed:
                state, reasoning, pending = "after", thought, rest
            else:
                # Keep back a tail that may be the beginning of the closing tag.
                keep = next(
                    (size for size in range(len(THINK_CLOSE) - 1, 0, -1) if pending.endswith(THINK_CLOSE[:size])), 0
                )
                reasoning, pending = pending[: len(pending) - keep], pending[len(pending) - keep :]
        if state == "after":
            reply, pending = pending.lstrip(), ""
            if reply:
                state = "reply"
        yield _with_text(chunk, reasoning, reply)
    if pending and state in {"start", "think"}:
        # The stream ended inside an unclosed block, or before the start was known.
        thinking = state == "think"
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content="" if thinking else pending, additional_kwargs={REASONING_KEY: pending} if thinking else {}
            )
        )


def _with_text(chunk: ChatGenerationChunk, reasoning: str, reply: str) -> ChatGenerationChunk:
    message = chunk.message
    kwargs = dict(message.additional_kwargs)
    if reasoning:
        kwargs[REASONING_KEY] = kwargs.get(REASONING_KEY, "") + reasoning
    return ChatGenerationChunk(
        message=message.model_copy(update={"content": reply, "additional_kwargs": kwargs}),
        generation_info=chunk.generation_info,
    )


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
