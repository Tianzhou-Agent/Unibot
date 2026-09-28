"""Policies around each model call: call budget, final request budget, output guards and malformed tool calls.

Token counting is injected (``count_tokens``), so the runtime carries no provider or product estimate of its own.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelCallLimitMiddleware, hook_config
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.messages.tool import InvalidToolCall
from langchain_core.utils.function_calling import convert_to_openai_tool

# Counts the tokens of an OpenAI-shaped request: (messages, tool schemas) -> tokens.
RequestTokenCounter = Callable[[list[dict[str, Any]], list[dict[str, Any]]], int]

TRUNCATED_FINISH_REASONS = frozenset({"length", "max_tokens"})


def reject_truncated_call(*, finish_reason: str | None) -> bool:
    """True when a model output was truncated and its tool calls must not execute."""
    return finish_reason in TRUNCATED_FINISH_REASONS


class ModelCallBudgetMiddleware(ModelCallLimitMiddleware):
    """The built-in per-invocation model-call limit, ending the run with the caller's ``reply``.

    The built-in either raises (leaving a failed step in the checkpoint) or ends with its own English notice.
    This ends cleanly with ``reply`` as the final message, so working memory and any archive agree; ``exceeded``
    holds the reply once the budget is spent. The count is per invocation, so an approval continuation gets a
    fresh budget.
    """

    def __init__(self, *, run_limit: int, reply: str) -> None:
        super().__init__(run_limit=run_limit, exit_behavior="end")
        self.reply = reply
        self.exceeded: str | None = None

    @hook_config(can_jump_to=["end"])
    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        update = super().before_model(state, runtime)
        if update is None:
            return None
        self.exceeded = self.reply
        return {**update, "messages": [AIMessage(content=self.reply)]}

    @hook_config(can_jump_to=["end"])
    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.before_model(state, runtime)


class RequestBudgetGuard(AgentMiddleware):
    """Refuse the provider when the final request exceeds the model's input budget.

    Runs as a model-call wrapper placed inside any scope/prompt policy, so it measures exactly what would be sent
    (system prompt, projected messages and advertised tool schemas). An oversized request is answered locally and
    never reaches the provider; ``exceeded`` holds the reply so the caller can fail the run while the original
    messages and tool results stay archived.
    """

    def __init__(self, *, input_budget_tokens: int, count_tokens: RequestTokenCounter) -> None:
        super().__init__()
        self.input_budget_tokens = input_budget_tokens
        self.count_tokens = count_tokens
        self.exceeded: str | None = None

    def _refusal(self, request: ModelRequest) -> ModelResponse | None:
        system = request.system_message.content if request.system_message is not None else ""
        total = self.count_tokens(
            [{"role": "system", "content": system}, *(_message_payload(message) for message in request.messages)],
            [convert_to_openai_tool(tool) for tool in request.tools],
        )
        if total <= self.input_budget_tokens:
            return None
        self.exceeded = (
            f"The conversation exceeds the model context budget ({total} estimated input tokens; "
            f"{self.input_budget_tokens} available after reserving space for the answer). "
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


class OutputGuardMiddleware(AgentMiddleware):
    """Fail empty and truncated responses; the calls of a truncated response never execute.

    Partial user-visible text is kept with a notice appended. ``failure`` holds the reply of a failed response, so
    the caller can fail the run. Its ``after_model`` hook must run before any hook that acts on tool calls
    (approvals, malformed-call handling): list it after them.
    """

    TRUNCATION_NOTICE = "[Incomplete response: the model reached its output limit. Please ask it to continue.]"
    EMPTY_REPLY = "The model returned an empty response."

    def __init__(self) -> None:
        super().__init__()
        self.failure: str | None = None

    def after_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = (state or {}).get("messages") or []
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage):
            return None
        calls = [*last.tool_calls, *last.invalid_tool_calls]
        if reject_truncated_call(finish_reason=(last.response_metadata or {}).get("finish_reason")):
            partial = _text(last)
            self.failure = f"{partial}\n\n{self.TRUNCATION_NOTICE}" if partial else self.TRUNCATION_NOTICE
            closures = [
                ToolMessage(
                    content=json.dumps(
                        {
                            "error": {
                                "code": "MODEL_OUTPUT_TRUNCATED",
                                "message": "The model response was incomplete; this tool call was not executed.",
                                "retryable": True,
                                "source": "model",
                            }
                        }
                    ),
                    tool_call_id=str(call.get("id") or ""),
                    name=str(call.get("name") or ""),
                    status="error",
                )
                for call in calls
            ]
            return {"messages": [*closures, AIMessage(content=self.failure)]}
        if not _text(last) and not calls:
            self.failure = self.EMPTY_REPLY
            return {"messages": [AIMessage(content=self.EMPTY_REPLY)]}
        return None

    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        return self.after_model(state, runtime)


class InvalidToolCallMiddleware(AgentMiddleware):
    """Answer tool calls whose arguments could not be parsed, instead of dropping them.

    Providers sometimes return arguments that are not valid JSON; LangChain keeps those calls in
    ``invalid_tool_calls``, where the tool node never sees them and nothing answers them. Each one gets an error
    result built by ``respond``, and the model is asked again when no valid call remains to run. Truncated
    responses are left to ``OutputGuardMiddleware``. List it before the other ``after_model`` policies so it runs
    last and a jump back to the model never skips them (e.g. the model-call count).
    """

    def __init__(self, respond: Callable[[InvalidToolCall], Awaitable[str]]) -> None:
        super().__init__()
        self.respond = respond

    @hook_config(can_jump_to=["model"])
    async def aafter_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        messages = (state or {}).get("messages") or []
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage) or not last.invalid_tool_calls:
            return None
        if reject_truncated_call(finish_reason=(last.response_metadata or {}).get("finish_reason")):
            return None
        results = [
            ToolMessage(
                content=await self.respond(call),
                tool_call_id=str(call.get("id")),
                name=str(call.get("name") or ""),
                status="error",
            )
            for call in last.invalid_tool_calls
            if call.get("id")
        ]
        update: dict[str, Any] = {"messages": results}
        if not last.tool_calls:
            update["jump_to"] = "model"
        return update


def _message_payload(message: BaseMessage) -> dict[str, Any]:
    payload: dict[str, Any] = {"role": message.type, "content": message.content}
    tool_calls = getattr(message, "tool_calls", None)
    if tool_calls:
        payload["tool_calls"] = tool_calls
    return payload


def _text(message: BaseMessage) -> str:
    return message.content if isinstance(message.content, str) else str(message.content or "")
