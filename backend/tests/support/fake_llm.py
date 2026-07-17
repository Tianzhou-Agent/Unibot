from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tianzhou_agent_platform.core.llm import EventSink, LLMResult


class ScriptedLLM:
    def __init__(self, responses: list[LLMResult | Callable[..., LLMResult]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: dict[str, Any] | str | None = None,
        event_sink: EventSink | None = None,
    ) -> LLMResult:
        call = {
            "messages": [dict(message) for message in messages],
            "tools": tools,
            "tool_choice": tool_choice,
        }
        self.calls.append(call)
        if not self.responses:
            raise AssertionError("The fake LLM received more calls than expected")
        response = self.responses.pop(0)
        result = response(messages=messages, tools=tools, tool_choice=tool_choice) if callable(response) else response
        if event_sink is not None and result.message.get("content"):
            await event_sink({"type": "message.delta", "delta": result.message["content"]})
        return result


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
        candidates = [
            item["function"]["name"]
            for item in tools
            if (prefix is None or item["function"]["name"].startswith(prefix))
            and (description_contains is None or description_contains in item["function"].get("description", ""))
        ]
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
