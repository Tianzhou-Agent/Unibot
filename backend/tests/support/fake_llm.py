"""Scripted native chat model used by tests.

Implements the LangChain ``BaseChatModel`` contract so ``create_agent`` can
drive it. Historical helper names (``ScriptedLLM``, ``assistant``,
``call_first_tool``) are preserved for regression suites.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import Field, PrivateAttr


def _wire_tool_choice(tool_choice: Any) -> Any:
    """Native tool_choice -> the OpenAI shape regression tests assert on."""
    if isinstance(tool_choice, str) and tool_choice not in {"auto", "any", "none", "required"}:
        return {"type": "function", "function": {"name": tool_choice}}
    return tool_choice


@dataclass
class LLMResult:
    """Fixture result shape retained for regression suites."""

    message: dict[str, Any]
    input_tokens: int = 0
    output_tokens: int = 0
    usage_estimated: bool = False
    finish_reason: str | None = None
    first_token_at: Any = None
    ttft_ms: float | None = None


def _to_ai_message(result: LLMResult | AIMessage | Any) -> AIMessage:
    if isinstance(result, AIMessage):
        return result
    if isinstance(result, LLMResult) or hasattr(result, "message"):
        message = result.message if hasattr(result, "message") else result
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []
        normalized = []
        for call in tool_calls:
            function = call.get("function") or {}
            args = function.get("arguments") or call.get("args") or "{}"
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args.strip() else {}
                except json.JSONDecodeError:
                    args = {"_raw": args}
            normalized.append(
                {
                    "name": function.get("name") or call.get("name"),
                    "args": args,
                    "id": call.get("id") or "call_1",
                }
            )
        finish = getattr(result, "finish_reason", None) or message.get("finish_reason")
        return AIMessage(
            content=content,
            tool_calls=normalized or [],
            response_metadata={"finish_reason": finish or ("tool_calls" if normalized else "stop")},
            usage_metadata=(
                {
                    "input_tokens": getattr(result, "input_tokens", 5),
                    "output_tokens": getattr(result, "output_tokens", 3),
                    "total_tokens": getattr(result, "input_tokens", 5) + getattr(result, "output_tokens", 3),
                }
                if hasattr(result, "input_tokens")
                else None
            ),
        )
    if isinstance(result, dict):
        return AIMessage(content=str(result.get("content") or ""))
    return AIMessage(content=str(result))


class ScriptedLLM(BaseChatModel):
    """Native BaseChatModel with the historical ``ScriptedLLM(responses)`` API."""

    model_name: str = Field(default="scripted-llm")
    responses: list[Any] = Field(default_factory=list)
    calls: list[dict[str, Any]] = Field(default_factory=list)
    _queue: list[Any] = PrivateAttr(default_factory=list)
    _bound: dict[str, Any] = PrivateAttr(default_factory=dict)

    def __init__(self, responses: list[Any] | None = None, **data: Any) -> None:
        items = list(responses or data.get("responses") or [])
        super().__init__(responses=items, **{k: v for k, v in data.items() if k != "responses"})
        # Queue and public ``responses`` must be the same list: tests extend ``llm.responses`` after setup.
        object.__setattr__(self, "_queue", self.responses)

    @property
    def _llm_type(self) -> str:
        return "scripted-llm"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> BaseChatModel:
        self._bound = {**self._bound, "tools": tools, "tool_choice": tool_choice, **kwargs}
        return self

    def bind(self, **kwargs: Any) -> BaseChatModel:
        self._bound = {**self._bound, **kwargs}
        return self

    def _advertised_tool_names(self) -> list[str]:
        tools = self._bound.get("tools") or []
        names: list[str] = []
        for item in tools:
            if isinstance(item, dict):
                function = item.get("function") or item
                names.append(str(function.get("name") or ""))
            else:
                names.append(str(getattr(item, "name", "")))
        return names

    def _next(self, messages: Sequence[BaseMessage], kwargs: dict[str, Any]) -> AIMessage:
        tools = kwargs.get("tools", self._bound.get("tools"))
        tool_choice = kwargs.get("tool_choice", self._bound.get("tool_choice"))
        wire_messages = []
        for message in messages:
            if isinstance(message, BaseMessage):
                item: dict[str, Any] = {
                    "role": getattr(message, "type", "assistant"),
                    "content": message.content if isinstance(message.content, str) else str(message.content or ""),
                }
                if message.type == "human":
                    item["role"] = "user"
                elif message.type == "ai":
                    item["role"] = "assistant"
                elif message.type == "tool":
                    item["role"] = "tool"
                    item["tool_call_id"] = getattr(message, "tool_call_id", None)
                    item["name"] = getattr(message, "name", None)
                elif message.type == "system":
                    item["role"] = "system"
                raw_calls = getattr(message, "tool_calls", None) or []
                if raw_calls:
                    item["tool_calls"] = [
                        {
                            "id": c.get("id"),
                            "type": "function",
                            "function": {
                                "name": c.get("name"),
                                "arguments": json.dumps(c.get("args") or {}, ensure_ascii=False),
                            },
                        }
                        for c in raw_calls
                    ]
                wire_messages.append(item)
            else:
                wire_messages.append(dict(message))  # type: ignore[arg-type]
        wire_tools = [convert_to_openai_tool(item) for item in (tools or [])]
        wire_tool_choice = _wire_tool_choice(tool_choice)
        self.calls.append(
            {
                "messages": wire_messages,
                "tools": wire_tools,
                "tool_choice": wire_tool_choice,
                "stop": kwargs.get("stop"),
                "context_type": kwargs.get("context_type") or "conversation",
            }
        )
        if not self._queue:
            raise AssertionError("The fake LLM received more calls than expected")
        item = self._queue.pop(0)
        if callable(item):
            # Historical callables receive the OpenAI wire shape the tests assert on.
            if inspect.signature(item).parameters:
                result = item(messages=wire_messages, tools=wire_tools, tool_choice=wire_tool_choice)
            else:
                result = item()
            return _to_ai_message(result)
        return _to_ai_message(item)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self._next(messages, {**kwargs, "stop": stop, "context_type": _context_type(run_manager)})
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        message = self._next(messages, {**kwargs, "stop": stop, "context_type": _context_type(run_manager)})
        content = message.content if isinstance(message.content, str) else str(message.content or "")
        yield ChatGenerationChunk(
            message=AIMessageChunk(
                content=content,
                tool_calls=message.tool_calls or [],
                response_metadata=message.response_metadata,
                usage_metadata=message.usage_metadata,
            )
        )

    async def _astream(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> Any:
        for chunk in self._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
            yield chunk

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None = None,
        event_sink: Any | None = None,
        **_: Any,
    ) -> LLMResult:
        """Legacy-shaped entry point used by remaining dual-path callers."""
        native_messages: list[BaseMessage] = []
        for item in messages:
            role = item.get("role")
            content = item.get("content") or ""
            if role == "user":
                from langchain_core.messages import HumanMessage

                native_messages.append(HumanMessage(content=content))
            elif role == "system":
                from langchain_core.messages import SystemMessage

                native_messages.append(SystemMessage(content=content))
            elif role == "tool":
                from langchain_core.messages import ToolMessage

                native_messages.append(
                    ToolMessage(content=content, tool_call_id=item.get("tool_call_id") or "c1", name=item.get("name") or "")
                )
            else:
                native_messages.append(AIMessage(content=content, tool_calls=[]))
        self._bound = {**self._bound, "tools": tools, "tool_choice": tool_choice}
        message = self._next(native_messages, {"tools": tools, "tool_choice": tool_choice})
        if event_sink is not None and message.content:
            await event_sink({"type": "message.delta", "delta": str(message.content)})
        tool_calls = []
        for call in message.tool_calls or []:
            tool_calls.append(
                {
                    "id": call.get("id"),
                    "type": "function",
                    "function": {
                        "name": call.get("name"),
                        "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
                    },
                }
            )
        return LLMResult(
            message={
                "role": "assistant",
                "content": message.content if isinstance(message.content, str) else "",
                "tool_calls": tool_calls or None,
            },
            input_tokens=5,
            output_tokens=3,
            finish_reason="tool_calls" if tool_calls else "stop",
        )


def assistant(content: str, *, input_tokens: int = 5, output_tokens: int = 3) -> LLMResult:
    return LLMResult(
        message={"role": "assistant", "content": content},
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        finish_reason="stop",
    )


def call_first_tool(
    *,
    arguments: str = "{}",
    call_id: str = "call_1",
    prefix: str | None = None,
    description_contains: str | None = None,
) -> Callable[..., LLMResult]:
    def response(*, tools: list[dict[str, Any]], **_: Any) -> LLMResult:
        candidates = []
        for item in tools or []:
            if isinstance(item, dict):
                function = item.get("function") or item
                name = str(function.get("name") or "")
                description = str(function.get("description") or "")
            else:
                name = str(getattr(item, "name", ""))
                description = str(getattr(item, "description", ""))
            if (prefix is None or name.startswith(prefix)) and (
                description_contains is None or description_contains in description
            ):
                candidates.append(name)
        if not candidates:
            raise AssertionError(
                f"No advertised tool matched prefix {prefix!r} and description {description_contains!r}"
            )
        return LLMResult(
            message={
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": candidates[0], "arguments": arguments},
                    }
                ],
            },
            finish_reason="tool_calls",
        )

    return response


# Alias used by native integration tests.
ScriptedChatModel = ScriptedLLM


def tool_calling(name: str, args: dict[str, Any], *, call_id: str = "call_1", content: str = "") -> LLMResult:
    return LLMResult(
        message={
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}
            ],
        },
        finish_reason="tool_calls",
    )


def multi_tool_calling(calls: Sequence[tuple[str, dict[str, Any], str]], *, content: str = "") -> LLMResult:
    return LLMResult(
        message={
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": name, "arguments": json.dumps(args)},
                }
                for name, args, call_id in calls
            ],
        },
        finish_reason="tool_calls",
    )


def _context_type(run_manager: Any) -> str | None:
    """Callers tag standalone model calls (e.g. compression) through run metadata."""
    metadata = getattr(run_manager, "metadata", None) or {}
    return metadata.get("context_type")
