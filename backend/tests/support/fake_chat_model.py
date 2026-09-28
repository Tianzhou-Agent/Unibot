"""Scripted native chat model used to drive create_agent without a live provider.

Implements the LangChain ``BaseChatModel`` contract so middleware, tools,
interrupts and streams can be exercised against real framework components.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field, PrivateAttr


class ScriptedChatModel(BaseChatModel):
    """Return a predetermined sequence of AIMessage responses.

    Each entry is either an ``AIMessage`` or a callable ``(messages, **kwargs) ->
    AIMessage``. Calls record the full request for assertions.
    """

    model_name: str = Field(default="scripted-chat")
    responses: list[Any] = Field(default_factory=list)
    calls: list[dict[str, Any]] = Field(default_factory=list)

    _queue: list[Any] = PrivateAttr(default_factory=list)
    _bound: dict[str, Any] = PrivateAttr(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        object.__setattr__(self, "_queue", list(self.responses))

    @property
    def _llm_type(self) -> str:
        return "scripted-chat"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    def bind_tools(self, tools: Any, *, tool_choice: Any = None, **kwargs: Any) -> BaseChatModel:
        self._bound = {**self._bound, "tools": tools, "tool_choice": tool_choice, **kwargs}
        return self

    def bind(self, **kwargs: Any) -> BaseChatModel:
        self._bound = {**self._bound, **kwargs}
        return self

    def _next(self, messages: Sequence[BaseMessage], kwargs: dict[str, Any]) -> AIMessage:
        self.calls.append(
            {
                "messages": list(messages),
                "tools": kwargs.get("tools", self._bound.get("tools")),
                "tool_choice": kwargs.get("tool_choice", self._bound.get("tool_choice")),
                "stop": kwargs.get("stop"),
            }
        )
        if not self._queue:
            raise AssertionError("ScriptedChatModel received more calls than expected")
        item = self._queue.pop(0)
        if callable(item):
            return item(messages=list(messages), **kwargs)
        return item

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        message = self._next(messages, {**kwargs, "stop": stop})
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
        message = self._next(messages, {**kwargs, "stop": stop})
        content = message.content if isinstance(message.content, str) else str(message.content or "")
        yield ChatGenerationChunk(
            message=AIMessageChunk(content=content, tool_calls=message.tool_calls or [])
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


def assistant(content: str, *, usage: dict[str, Any] | None = None) -> AIMessage:
    meta = usage or {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}
    return AIMessage(
        content=content,
        usage_metadata={
            "input_tokens": meta.get("input_tokens", 5),
            "output_tokens": meta.get("output_tokens", 3),
            "total_tokens": meta.get("total_tokens", 8),
        },
        response_metadata={"finish_reason": "stop"},
    )


def tool_calling(
    name: str,
    args: dict[str, Any],
    *,
    call_id: str = "call_1",
    content: str = "",
) -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=[{"name": name, "args": args, "id": call_id}],
        response_metadata={"finish_reason": "tool_calls"},
    )


def multi_tool_calling(
    calls: Sequence[tuple[str, dict[str, Any], str]],
    *,
    content: str = "",
) -> AIMessage:
    """Build one AIMessage with several tool calls: (name, args, call_id)."""
    return AIMessage(
        content=content,
        tool_calls=[{"name": n, "args": a, "id": i} for n, a, i in calls],
        response_metadata={"finish_reason": "tool_calls"},
    )
