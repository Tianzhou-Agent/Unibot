from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
import openai
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.model_settings import current_model_runtime

EventSink = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass(slots=True)
class LLMResult:
    message: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None


class LLMClient(Protocol):
    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None = None,
        event_sink: EventSink | None = None,
    ) -> LLMResult: ...


class OpenAICompatibleClient:
    """LangChain adapter that preserves the platform's provider-neutral LLM port."""

    def __init__(self, settings: AgentSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _chat_model(self) -> ChatOpenAI:
        runtime_model = current_model_runtime()
        base_url = runtime_model.base_url if runtime_model else self.settings.llm_base_url
        api_key = (
            runtime_model.api_key
            if runtime_model
            else self.settings.llm_api_key.get_secret_value()
            if self.settings.llm_api_key is not None
            else ""
        )
        model = runtime_model.model if runtime_model else self.settings.llm_model
        timeout_seconds = runtime_model.timeout_seconds if runtime_model else self.settings.llm_timeout_seconds
        if not base_url or not model:
            raise PlatformError(
                code="INVALID_REQUEST",
                message="The LLM provider is not configured",
                status_code=503,
                source="model",
                user_message="The language model is not configured for this service.",
            )
        return create_openai_chat_model(
            model=model,
            api_key=api_key,
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            client=self._client,
        )

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None = None,
        event_sink: EventSink | None = None,
    ) -> LLMResult:
        # Several OpenAI-compatible providers reject named tool_choice while
        # streaming. Forced tool turns have no user-facing text, so keep them
        # on the non-streaming LangChain path.
        if event_sink is not None and _tool_choice_name(tool_choice) is None:
            return await self._stream_complete(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                event_sink=event_sink,
            )
        return await self._invoke_with_fallback(messages, tools, tool_choice)

    async def _invoke_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None,
    ) -> LLMResult:
        try:
            message = await self._bound_model(tools, tool_choice).ainvoke(messages)
        except openai.BadRequestError as exc:
            if _tool_choice_name(tool_choice) is None or "tool_choice" not in str(exc).lower():
                raise _map_openai_error(exc) from exc
            fallback_messages = _add_forced_tool_instruction(messages, _tool_choice_name(tool_choice))
            try:
                message = await self._bound_model(tools, None).ainvoke(fallback_messages)
            except openai.OpenAIError as fallback_exc:
                raise _map_openai_error(fallback_exc) from fallback_exc
        except openai.OpenAIError as exc:
            raise _map_openai_error(exc) from exc
        return _result_from_message(message)

    async def _stream_complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None,
        event_sink: EventSink,
    ) -> LLMResult:
        aggregate: AIMessageChunk | None = None
        try:
            async for chunk in self._bound_model(tools, tool_choice).astream(messages):
                aggregate = chunk if aggregate is None else aggregate + chunk
                delta = _message_text(chunk.content)
                if delta:
                    await event_sink({"type": "message.delta", "delta": delta})
        except openai.OpenAIError as exc:
            raise _map_openai_error(exc) from exc
        if aggregate is None:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "The model provider returned an empty stream",
                status_code=502,
                source="model",
            )
        return _result_from_message(aggregate)

    def _bound_model(
        self,
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None,
    ) -> Any:
        model = self._chat_model()
        if not tools:
            return model
        return model.bind_tools(tools, tool_choice=tool_choice)


def _result_from_message(message: AIMessage | AIMessageChunk) -> LLMResult:
    normalized: dict[str, Any] = {
        "role": "assistant",
        "content": _message_text(message.content),
    }
    tool_calls: list[dict[str, Any]] = []
    for index, call in enumerate(message.tool_calls):
        arguments = call.get("args") or {}
        tool_calls.append(
            {
                "id": call.get("id") or f"call_{index}",
                "type": "function",
                "function": {
                    "name": str(call.get("name") or ""),
                    "arguments": json.dumps(arguments, ensure_ascii=False),
                },
            }
        )
    if tool_calls:
        normalized["tool_calls"] = tool_calls
    usage: dict[str, Any] = dict(message.usage_metadata or {})
    return LLMResult(
        message=normalized,
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        finish_reason=str(message.response_metadata.get("finish_reason") or "") or None,
    )


def _message_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        if parts:
            return "".join(parts)
    return json.dumps(content, ensure_ascii=False)


def create_openai_chat_model(
    *,
    model: str,
    api_key: str,
    base_url: str,
    timeout_seconds: float,
    client: httpx.AsyncClient,
    max_completion_tokens: int | None = None,
) -> ChatOpenAI:
    """Build the single LangChain OpenAI integration used by chat and health checks."""

    return ChatOpenAI(
        model=model,
        api_key=SecretStr(api_key or "not-configured"),
        base_url=_openai_base_url(base_url),
        timeout=timeout_seconds,
        max_retries=0,
        http_async_client=client,
        max_completion_tokens=max_completion_tokens,
        use_responses_api=False,
    )


def _openai_base_url(value: str) -> str:
    normalized = value.rstrip("/")
    suffix = "/chat/completions"
    return normalized[: -len(suffix)] if normalized.endswith(suffix) else normalized


def _map_openai_error(exc: openai.OpenAIError) -> PlatformError:
    if isinstance(exc, openai.APITimeoutError):
        return PlatformError(
            "TIMEOUT",
            "The model request timed out",
            status_code=504,
            retryable=True,
            source="model",
        )
    if isinstance(exc, openai.APIConnectionError):
        return PlatformError(
            "DEPENDENCY_FAILED",
            "The model provider could not be reached",
            status_code=502,
            retryable=True,
            source="model",
        )
    status_code = getattr(exc, "status_code", None)
    return PlatformError(
        "DEPENDENCY_FAILED",
        f"The model provider returned HTTP {status_code}" if status_code else "The model request failed",
        status_code=502,
        retryable=status_code is not None and (status_code >= 500 or status_code == 429),
        source="model",
        debug={"provider_status": status_code} if status_code is not None else {},
    )


def _tool_choice_name(tool_choice: dict[str, Any] | str | None) -> str | None:
    if not isinstance(tool_choice, dict):
        return None
    function = tool_choice.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    return str(name) if name else None


def _add_forced_tool_instruction(
    messages: list[dict[str, Any]],
    function_name: str | None,
) -> list[dict[str, Any]]:
    if function_name is None:
        return messages
    directive = f"The caller explicitly selected function {function_name}. Call that function before answering the user."
    copied = [dict(message) for message in messages]
    if copied and copied[0].get("role") == "system":
        copied[0]["content"] = f"{copied[0].get('content') or ''}\n\n{directive}"
    else:
        copied.insert(0, {"role": "system", "content": directive})
    return copied
