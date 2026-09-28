"""Final request budget and output guards.

Keeps request-budget checks out of the custom loop. Shared counting helpers
live in model_providers.tokens when observability also needs them.
"""

from __future__ import annotations

import json
import math
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.utils.function_calling import convert_to_openai_tool


def request_input_budget(context_window_tokens: int, *, output_reserve: int) -> int:
    """Tokens available for the final projected request (messages + tool schemas)."""
    return max(0, context_window_tokens - output_reserve)


def reject_truncated_call(*, finish_reason: str | None) -> bool:
    """True when a model output was truncated and tool calls must not execute."""
    return finish_reason in {"length", "max_tokens"}


def default_output_reserve(context_window_tokens: int) -> int:
    """Answer space kept free in every request: a quarter of the window, at most 4096 tokens."""
    return min(4_096, context_window_tokens // 4)


class RequestBudgetGuard(AgentMiddleware):
    """Refuse the provider when the final request exceeds the context budget (acceptance A4).

    Runs as a model-call wrapper placed inside any scope/prompt policy, so it measures exactly what would be
    sent (system prompt, projected messages and advertised tool schemas) with space reserved for the answer.
    An oversized request is answered locally and never reaches the provider; ``exceeded`` holds the reply so
    the caller can fail the run while the original messages and tool results stay archived.
    """

    def __init__(self, *, context_window_tokens: int, output_reserve: int | None = None) -> None:
        super().__init__()
        self.context_window_tokens = context_window_tokens
        self.output_reserve = (
            output_reserve if output_reserve is not None else default_output_reserve(context_window_tokens)
        )
        self.exceeded: str | None = None

    def _refusal(self, request: ModelRequest) -> ModelResponse | None:
        system = request.system_message.content if request.system_message is not None else ""
        total = estimate_request_tokens(
            [{"role": "system", "content": system}, *(_message_payload(message) for message in request.messages)],
            [convert_to_openai_tool(tool) for tool in request.tools],
        )
        budget = request_input_budget(self.context_window_tokens, output_reserve=self.output_reserve)
        if total <= budget:
            return None
        self.exceeded = (
            f"The conversation exceeds the model context budget ({total} estimated input tokens; "
            f"{budget} available after reserving space for the answer). "
            "The original messages and tool results were preserved. Narrow the request or use a model "
            "with a larger context window to continue."
        )
        return ModelResponse(result=[AIMessage(content=self.exceeded)])

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return self._refusal(request) or await handler(request)

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return self._refusal(request) or handler(request)


def estimate_request_tokens(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> int:
    """Conservative tokenizer-free estimate for multilingual JSON chat payloads (same rule as the product)."""
    total = sum(_estimate_value_tokens(message) + 4 for message in messages)
    if tools:
        total += _estimate_value_tokens(tools)
    return total


def _estimate_value_tokens(value: Any) -> int:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    ascii_count = sum(character.isascii() for character in text)
    return math.ceil(ascii_count / 4) + (len(text) - ascii_count)


def _message_payload(message: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": getattr(message, "type", "user"), "content": getattr(message, "content", "")}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return payload


class OutputGuardMiddleware(AgentMiddleware):
    """Fail empty/truncated responses; never execute partial tool calls.

    Partial user-visible text is preserved and a recovery notice is appended.
    """

    TRUNCATION_NOTICE = (
        "[Incomplete response: the model reached its output limit. "
        "Please ask it to continue.]"
    )

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = (state or {}).get("messages") or []
        last = messages[-1] if messages else None
        if last is None:
            return None

        finish_reason = None
        if isinstance(last, AIMessage):
            meta = last.response_metadata or {}
            finish_reason = meta.get("finish_reason")
        elif isinstance(last, dict):
            meta = last.get("response_metadata") or {}
            finish_reason = meta.get("finish_reason")

        tool_calls = getattr(last, "tool_calls", None) or (
            last.get("tool_calls") if isinstance(last, dict) else None
        ) or []

        if reject_truncated_call(finish_reason=finish_reason):
            updates: list[Any] = []
            notice = self.TRUNCATION_NOTICE
            for call in tool_calls:
                call_id = str(call.get("id") or "")
                name = str(call.get("name") or "")
                updates.append(
                    ToolMessage(
                        content='{"error": {"code": "MODEL_OUTPUT_TRUNCATED", "message": "The model response was incomplete; this tool call was not executed.", "retryable": true, "source": "model"}}',
                        tool_call_id=call_id,
                        name=name,
                        status="error",
                    )
                )
            partial = ""
            if isinstance(last, AIMessage):
                partial = last.content if isinstance(last.content, str) else str(last.content or "")
            elif isinstance(last, dict):
                partial = str(last.get("content") or "")
            content = f"{partial}\n\n{notice}" if partial else notice
            updates.append(AIMessage(content=content))
            return {
                "messages": updates,
                "final_status": "failed",
                "final_content": content,
            }

        content_text = ""
        if isinstance(last, AIMessage):
            content_text = last.content if isinstance(last.content, str) else str(last.content or "")
        elif isinstance(last, dict):
            content_text = str(last.get("content") or "")
        if not content_text and not tool_calls:
            return {
                "messages": [AIMessage(content="The model returned an empty response.")],
                "final_status": "failed",
                "final_content": "The model returned an empty response.",
            }
        return None

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.after_model(state, runtime)
