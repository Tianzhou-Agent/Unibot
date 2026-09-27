"""Final request budget and output guards.

Keeps request-budget checks out of the custom loop. Shared counting helpers
live in model_providers.tokens when observability also needs them.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import AgentMiddleware, hook_config
from langchain_core.messages import AIMessage, ToolMessage


def request_input_budget(context_window_tokens: int, *, output_reserve: int) -> int:
    """Tokens available for the final projected request (messages + tool schemas)."""
    return max(0, context_window_tokens - output_reserve)


def reject_truncated_call(*, finish_reason: str | None) -> bool:
    """True when a model output was truncated and tool calls must not execute."""
    return finish_reason in {"length", "max_tokens"}


class RequestBudgetGuard(AgentMiddleware):
    """Refuse the provider when the final projected request exceeds the budget.

    Checks messages + system prompt + tool definitions + output reserve before
    every model call (acceptance A4). Oversized content is never sent.
    """

    def __init__(
        self,
        *,
        context_window_tokens: int,
        output_reserve: int = 4096,
        estimate: Any | None = None,
    ) -> None:
        self.context_window_tokens = context_window_tokens
        self.output_reserve = output_reserve
        self._estimate = estimate

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        estimate = self._estimate or _estimate_message_tokens
        messages = (state or {}).get("messages") or []
        tools = (state or {}).get("tool_definitions") or []
        context = {}
        if runtime is not None:
            context = getattr(runtime, "context", None) or {}
        system_prompt = context.get("system_prompt") or ""
        total = estimate(messages) + _estimate_tools_tokens(tools)
        if system_prompt:
            total += max(1, len(str(system_prompt)) // 4)
        budget = request_input_budget(self.context_window_tokens, output_reserve=self.output_reserve)
        if total <= budget:
            return None
        content = (
            f"The conversation exceeds the model context budget ({total} estimated input tokens; "
            f"{budget} available after reserving space for the answer). "
            "The original messages and tool results were preserved. Narrow the request or use a model "
            "with a larger context window to continue."
        )
        return {
            "messages": [AIMessage(content=content)],
            "jump_to": "end",
            "final_status": "failed",
            "final_content": content,
        }

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.before_model(state, runtime)


def _estimate_message_tokens(messages: Any) -> int:
    total = 0
    for message in messages or []:
        if isinstance(message, str):
            total += max(1, len(message) // 4)
            continue
        content = getattr(message, "content", None)
        if isinstance(content, str):
            total += max(1, len(content) // 4)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, str):
                    total += max(1, len(block) // 4)
                elif isinstance(block, dict):
                    total += max(1, len(str(block.get("text", ""))) // 4)
        total += 4
    return total


def _estimate_tools_tokens(tools: Any) -> int:
    import json

    if not tools:
        return 0
    try:
        payload = json.dumps(tools, default=str)
    except TypeError:
        payload = str(tools)
    return max(1, len(payload) // 4)


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
