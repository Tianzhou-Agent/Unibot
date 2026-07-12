from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError

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
    def __init__(self, settings: AgentSettings, client: httpx.AsyncClient | None = None) -> None:
        self.settings = settings
        self._client = client or httpx.AsyncClient()
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _request_parts(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None,
        stream: bool,
    ) -> tuple[str, dict[str, str], dict[str, Any]]:
        url = self.settings.chat_completions_url
        api_key = self.settings.llm_api_key
        model = self.settings.llm_model
        if not url or api_key is None or not model:
            raise PlatformError(
                code="INVALID_REQUEST",
                message="The LLM provider is not configured",
                status_code=503,
                source="model",
                user_message="The language model is not configured for this service.",
            )
        headers = {
            "Authorization": f"Bearer {api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {"model": model, "messages": messages, "stream": stream}
        if tools:
            body["tools"] = tools
            if tool_choice is not None:
                body["tool_choice"] = tool_choice
        return url, headers, body

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None = None,
        event_sink: EventSink | None = None,
    ) -> LLMResult:
        # Several OpenAI-compatible providers reject named tool_choice when
        # streaming. A forced tool turn has no user-facing text to stream, so
        # use the compatible non-streaming path and resume SSE for the model's
        # answer after the tool result is available.
        if event_sink is not None and _tool_choice_name(tool_choice) is None:
            return await self._stream_complete(
                messages=messages,
                tools=tools,
                tool_choice=tool_choice,
                event_sink=event_sink,
            )

        url, headers, body = self._request_parts(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            stream=False,
        )
        try:
            response = await self._client.post(
                url,
                headers=headers,
                json=body,
                timeout=self.settings.llm_timeout_seconds,
            )
        except httpx.TimeoutException as exc:
            raise PlatformError(
                "TIMEOUT",
                "The model request timed out",
                status_code=504,
                retryable=True,
                source="model",
            ) from exc
        except httpx.RequestError as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "The model provider could not be reached",
                status_code=502,
                retryable=True,
                source="model",
            ) from exc
        if response.is_error and _tool_choice_is_unsupported(response, tool_choice):
            fallback_body = dict(body)
            fallback_body.pop("tool_choice", None)
            fallback_body["messages"] = _add_forced_tool_instruction(
                messages,
                _tool_choice_name(tool_choice),
            )
            try:
                response = await self._client.post(
                    url,
                    headers=headers,
                    json=fallback_body,
                    timeout=self.settings.llm_timeout_seconds,
                )
            except httpx.TimeoutException as exc:
                raise PlatformError(
                    "TIMEOUT",
                    "The model request timed out",
                    status_code=504,
                    retryable=True,
                    source="model",
                ) from exc
            except httpx.RequestError as exc:
                raise PlatformError(
                    "DEPENDENCY_FAILED",
                    "The model provider could not be reached",
                    status_code=502,
                    retryable=True,
                    source="model",
                ) from exc
        if response.is_error:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                f"The model provider returned HTTP {response.status_code}",
                status_code=502,
                retryable=response.status_code >= 500 or response.status_code == 429,
                source="model",
                debug={"provider_status": response.status_code},
            )
        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = _normalize_message(choice["message"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "The model provider returned an invalid chat-completions response",
                status_code=502,
                source="model",
            ) from exc
        usage = payload.get("usage") or {}
        return LLMResult(
            message=message,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            finish_reason=choice.get("finish_reason"),
        )

    async def _stream_complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None,
        event_sink: EventSink,
    ) -> LLMResult:
        url, headers, body = self._request_parts(
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            stream=True,
        )
        content_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}
        input_tokens = 0
        output_tokens = 0
        finish_reason: str | None = None
        try:
            async with self._client.stream(
                "POST",
                url,
                headers=headers,
                json=body,
                timeout=self.settings.llm_timeout_seconds,
            ) as response:
                if response.is_error:
                    await response.aread()
                    raise PlatformError(
                        "DEPENDENCY_FAILED",
                        f"The model provider returned HTTP {response.status_code}",
                        status_code=502,
                        retryable=response.status_code >= 500 or response.status_code == 429,
                        source="model",
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    usage = chunk.get("usage") or {}
                    input_tokens = int(usage.get("prompt_tokens") or input_tokens)
                    output_tokens = int(usage.get("completion_tokens") or output_tokens)
                    choices = chunk.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0]
                    finish_reason = choice.get("finish_reason") or finish_reason
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        if not isinstance(content, str):
                            content = json.dumps(content, ensure_ascii=False)
                        content_parts.append(content)
                        await event_sink({"type": "message.delta", "delta": content})
                    for call_delta in delta.get("tool_calls") or []:
                        index = int(call_delta.get("index") or 0)
                        call = tool_calls.setdefault(
                            index,
                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
                        )
                        if call_delta.get("id"):
                            call["id"] = call_delta["id"]
                        function = call_delta.get("function") or {}
                        call["function"]["name"] += function.get("name") or ""
                        call["function"]["arguments"] += function.get("arguments") or ""
        except PlatformError:
            raise
        except httpx.TimeoutException as exc:
            raise PlatformError(
                "TIMEOUT",
                "The streamed model request timed out",
                status_code=504,
                retryable=True,
                source="model",
            ) from exc
        except httpx.RequestError as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "The model provider stream failed",
                status_code=502,
                retryable=True,
                source="model",
            ) from exc

        normalized_calls = [tool_calls[index] for index in sorted(tool_calls)]
        for index, call in enumerate(normalized_calls):
            if not call["id"]:
                call["id"] = f"call_stream_{index}"
        message: dict[str, Any] = {"role": "assistant", "content": "".join(content_parts)}
        if normalized_calls:
            message["tool_calls"] = normalized_calls
        return LLMResult(
            message=message,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            finish_reason=finish_reason,
        )


def _normalize_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message.get("content")
    if content is None:
        content = ""
    elif not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    normalized: dict[str, Any] = {"role": "assistant", "content": content}
    calls: list[dict[str, Any]] = []
    for index, call in enumerate(message.get("tool_calls") or []):
        function = call.get("function") or {}
        calls.append(
            {
                "id": call.get("id") or f"call_{index}",
                "type": "function",
                "function": {
                    "name": str(function.get("name") or ""),
                    "arguments": function.get("arguments") or "{}",
                },
            }
        )
    if calls:
        normalized["tool_calls"] = calls
    return normalized


def _tool_choice_name(tool_choice: dict[str, Any] | str | None) -> str | None:
    if not isinstance(tool_choice, dict):
        return None
    function = tool_choice.get("function")
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    return str(name) if name else None


def _tool_choice_is_unsupported(
    response: httpx.Response,
    tool_choice: dict[str, Any] | str | None,
) -> bool:
    return (
        response.status_code == 400
        and _tool_choice_name(tool_choice) is not None
        and "tool_choice" in response.text.lower()
    )


def _add_forced_tool_instruction(
    messages: list[dict[str, Any]],
    function_name: str | None,
) -> list[dict[str, Any]]:
    if function_name is None:
        return messages
    directive = (
        f"The caller explicitly selected function {function_name}. " "Call that function before answering the user."
    )
    copied = [dict(message) for message in messages]
    if copied and copied[0].get("role") == "system":
        copied[0]["content"] = f"{copied[0].get('content') or ''}\n\n{directive}"
    else:
        copied.insert(0, {"role": "system", "content": directive})
    return copied
