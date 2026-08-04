from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Protocol
from uuid import uuid4

import httpx
import openai
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI
from pydantic import SecretStr

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.chat import LLMCallRecord
from tianzhou_agent_platform.core.context_compression import estimate_request_tokens
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.model_settings import current_model_runtime
from tianzhou_agent_platform.core.trace_details import sanitize_trace_data

EventSink = Callable[[dict[str, Any]], Awaitable[None]]
LLMCallSink = Callable[[LLMCallRecord], Awaitable[None]]
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LLMResult:
    message: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    finish_reason: str | None = None
    first_token_at: datetime | None = None
    ttft_ms: float | None = None


class LLMClient(Protocol):
    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None = None,
        event_sink: EventSink | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        context_type: str | None = None,
        context_id: str | None = None,
    ) -> LLMResult: ...


class OpenAICompatibleClient:
    """LangChain adapter that preserves the platform's provider-neutral LLM port."""

    def __init__(
        self,
        settings: AgentSettings,
        client: httpx.AsyncClient | None = None,
        *,
        call_sink: LLMCallSink | None = None,
    ) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None
        self._call_sink = call_sink

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
        trace_id: str | None = None,
        span_id: str | None = None,
        context_type: str | None = None,
        context_id: str | None = None,
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
                trace_id=trace_id,
                span_id=span_id,
                context_type=context_type,
                context_id=context_id,
            )
        return await self._invoke_with_fallback(
            messages,
            tools,
            tool_choice,
            trace_id=trace_id,
            span_id=span_id,
            context_type=context_type,
            context_id=context_id,
        )

    async def _invoke_with_fallback(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None,
        *,
        trace_id: str | None,
        span_id: str | None,
        context_type: str | None,
        context_id: str | None,
    ) -> LLMResult:
        try:
            return await self._invoke_once(
                messages,
                tools,
                tool_choice,
                trace_id=trace_id,
                span_id=span_id,
                context_type=context_type,
                context_id=context_id,
            )
        except openai.BadRequestError as exc:
            if _tool_choice_name(tool_choice) is None or "tool_choice" not in str(exc).lower():
                raise _map_openai_error(exc) from exc
            fallback_messages = _add_forced_tool_instruction(messages, _tool_choice_name(tool_choice))
            try:
                return await self._invoke_once(
                    fallback_messages,
                    tools,
                    None,
                    trace_id=trace_id,
                    span_id=span_id,
                    context_type=context_type,
                    context_id=context_id,
                )
            except openai.OpenAIError as fallback_exc:
                raise _map_openai_error(fallback_exc) from fallback_exc
        except openai.OpenAIError as exc:
            raise _map_openai_error(exc) from exc

    async def _invoke_once(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None,
        *,
        trace_id: str | None,
        span_id: str | None,
        context_type: str | None,
        context_id: str | None,
    ) -> LLMResult:
        call, started = await self._start_call(
            messages,
            tools,
            tool_choice,
            stream=False,
            trace_id=trace_id,
            span_id=span_id,
            context_type=context_type,
            context_id=context_id,
        )
        try:
            message = await self._bound_model(tools, tool_choice).ainvoke(messages)
        except openai.OpenAIError as exc:
            await self._fail_call(call, started, exc)
            raise
        except Exception as exc:
            await self._fail_call(call, started, exc)
            raise
        result = _result_from_message(message)
        await self._complete_call(call, started, result)
        return result

    async def _stream_complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None,
        event_sink: EventSink,
        trace_id: str | None,
        span_id: str | None,
        context_type: str | None,
        context_id: str | None,
    ) -> LLMResult:
        call, started = await self._start_call(
            messages,
            tools,
            tool_choice,
            stream=True,
            trace_id=trace_id,
            span_id=span_id,
            context_type=context_type,
            context_id=context_id,
        )
        aggregate: AIMessageChunk | None = None
        first_token_at: datetime | None = None
        ttft_ms: float | None = None
        try:
            async for chunk in self._bound_model(tools, tool_choice).astream(messages):
                aggregate = chunk if aggregate is None else aggregate + chunk
                delta = _message_text(chunk.content)
                if delta:
                    if first_token_at is None:
                        first_token_at = datetime.now(UTC)
                        ttft_ms = max(0.0, (perf_counter() - started) * 1000)
                    await event_sink({"type": "message.delta", "delta": delta})
        except openai.OpenAIError as exc:
            await self._fail_call(call, started, exc)
            raise _map_openai_error(exc) from exc
        except Exception as exc:
            await self._fail_call(call, started, exc)
            raise
        if aggregate is None:
            error = PlatformError(
                "DEPENDENCY_FAILED",
                "The model provider returned an empty stream",
                status_code=502,
                source="model",
            )
            await self._fail_call(call, started, error)
            raise error
        result = _result_from_message(aggregate)
        result.first_token_at = first_token_at
        result.ttft_ms = ttft_ms
        await self._complete_call(call, started, result)
        return result

    async def _start_call(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None,
        *,
        stream: bool,
        trace_id: str | None,
        span_id: str | None,
        context_type: str | None,
        context_id: str | None,
    ) -> tuple[LLMCallRecord, float]:
        endpoint, model = self._request_target()
        request: dict[str, Any] = {
            "model": model,
            "messages": deepcopy(messages),
            "stream": stream,
            "context_window": self.settings.context_window_tokens,
            "estimated_prompt_tokens": estimate_request_tokens(messages, tools),
        }
        if tools:
            request["tools"] = deepcopy(tools)
        if tool_choice is not None:
            request["tool_choice"] = deepcopy(tool_choice)
        request = sanitize_trace_data(request)
        call = LLMCallRecord(
            call_id=f"llm_{uuid4().hex}",
            trace_id=trace_id,
            span_id=span_id,
            context_type=context_type,
            context_id=context_id,
            endpoint=endpoint,
            model=model,
            request=request,
        )
        await self._record_call(call)
        return call, perf_counter()

    async def _complete_call(self, call: LLMCallRecord, started: float, result: LLMResult) -> None:
        completed_at = datetime.now(UTC)
        completed = call.model_copy(
            update={
                "status": "completed",
                "response": sanitize_trace_data(_response_from_result(call.model, result)),
                "duration_ms": (perf_counter() - started) * 1000,
                "first_token_at": result.first_token_at,
                "ttft_ms": result.ttft_ms,
                "completed_at": completed_at,
            }
        )
        await self._record_call(completed)

    async def _fail_call(self, call: LLMCallRecord, started: float, exc: Exception) -> None:
        completed_at = datetime.now(UTC)
        failed = call.model_copy(
            update={
                "status": "failed",
                "response": sanitize_trace_data(_response_from_error(exc)),
                "duration_ms": (perf_counter() - started) * 1000,
                "error": sanitize_trace_data(str(exc)),
                "completed_at": completed_at,
            }
        )
        await self._record_call(failed)

    async def _record_call(self, call: LLMCallRecord) -> None:
        if self._call_sink is None:
            return
        try:
            await self._call_sink(call)
        except Exception:
            logger.exception("Could not persist LLM call %s", call.call_id)

    def _request_target(self) -> tuple[str, str]:
        runtime_model = current_model_runtime()
        base_url = runtime_model.base_url if runtime_model else self.settings.llm_base_url
        model = runtime_model.model if runtime_model else self.settings.llm_model
        if not base_url or not model:
            raise PlatformError(
                code="INVALID_REQUEST",
                message="The LLM provider is not configured",
                status_code=503,
                source="model",
                user_message="The language model is not configured for this service.",
            )
        normalized = base_url.rstrip("/")
        endpoint = normalized if normalized.endswith("/chat/completions") else f"{normalized}/chat/completions"
        return endpoint, model

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


def _response_from_result(model: str, result: LLMResult) -> dict[str, Any]:
    return {
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": deepcopy(result.message),
                "finish_reason": result.finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": result.input_tokens,
            "completion_tokens": result.output_tokens,
            "total_tokens": result.input_tokens + result.output_tokens,
        },
    }


def _response_from_error(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, openai.APIStatusError):
        response: dict[str, Any] = {"status_code": exc.status_code}
        try:
            body = exc.response.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            body = exc.response.text
        response["body"] = body
        return response
    if isinstance(exc, PlatformError):
        return {
            "error": {
                "code": exc.code,
                "message": exc.message,
                "source": exc.source,
            }
        }
    return {"error": {"type": type(exc).__name__, "message": str(exc)}}


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
