from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Sequence, cast
from uuid import uuid4

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, convert_to_openai_messages
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable
from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict

from tianzhou_agent_platform.core.chat import TraceEvent
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.llm import EventSink, LLMResult
from tianzhou_agent_platform.core.model_settings import current_model_runtime
from tianzhou_agent_platform.core.trace_details import sanitize_trace_data


@dataclass(slots=True)
class ModelRunContext:
    repository: Any
    trace_id: str
    capabilities: dict[str, Any]
    event_sink: EventSink | None = None
    iterations: int = 0
    usage_input: int = 0
    usage_output: int = 0
    invalid_tool_arguments: dict[str, str] = field(default_factory=dict)
    empty_response: bool = False
    record_local_trace: bool = True


class LangChainChatModel(BaseChatModel):
    """Expose the platform's provider client through LangChain's chat-model API."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    client: Any
    context: ModelRunContext
    default_model_name: str | None = None

    @property
    def _llm_type(self) -> str:
        return "unibot-openai-compatible"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        runtime_model = current_model_runtime()
        return {"model_name": runtime_model.model if runtime_model else self.default_model_name}

    def bind_tools(
        self,
        tools: Sequence[dict[str, Any] | type | Any | BaseTool],
        *,
        tool_choice: str | dict[str, Any] | bool | None = None,
        **kwargs: Any,
    ) -> Runnable[Any, AIMessage]:
        definitions = [convert_to_openai_tool(tool) for tool in tools]
        return self.bind(tools=definitions, tool_choice=tool_choice, **kwargs)

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del messages, stop, run_manager, kwargs
        raise NotImplementedError("Unibot's model client is asynchronous")

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        del stop, run_manager
        iteration = self.context.iterations + 1
        started = perf_counter()
        runtime_model = current_model_runtime()
        model_target_id = runtime_model.model if runtime_model else self.default_model_name
        tools = list(kwargs.get("tools") or [])
        tool_choice = kwargs.get("tool_choice")
        wire_messages = convert_to_openai_messages(messages)
        if isinstance(wire_messages, dict):
            wire_messages = [wire_messages]

        if self.context.record_local_trace:
            await self.context.repository.add_trace_event(
                self.context.trace_id,
                TraceEvent(
                    kind="model.requested",
                    status="started",
                    target_type="model",
                    target_id=model_target_id,
                    details={
                        "iteration": iteration,
                        "message_count": len(wire_messages),
                        "message_roles": [str(message.get("role") or "unknown") for message in wire_messages],
                        "capability_ids": sorted(
                            {capability.capability_id for capability in self.context.capabilities.values()}
                        ),
                        "forced_function": _tool_choice_name(tool_choice),
                        "streaming": self.context.event_sink is not None,
                    },
                ),
            )

        try:
            result = await self.client.complete(
                messages=wire_messages,
                tools=tools,
                tool_choice=tool_choice,
                event_sink=self.context.event_sink,
            )
        except PlatformError as exc:
            if self.context.record_local_trace:
                await self.context.repository.add_trace_event(
                    self.context.trace_id,
                    TraceEvent(
                        kind="model.failed",
                        status="failed",
                        target_type="model",
                        target_id=model_target_id,
                        duration_ms=(perf_counter() - started) * 1000,
                        details={
                            "iteration": iteration,
                            "code": exc.code,
                            "message": sanitize_trace_data(exc.message),
                            "retryable": exc.retryable,
                        },
                    ),
                )
            raise

        message = _to_ai_message(result, self.context)
        self.context.iterations = iteration
        self.context.usage_input += result.input_tokens
        self.context.usage_output += result.output_tokens
        if self.context.record_local_trace:
            await self.context.repository.add_trace_event(
                self.context.trace_id,
                TraceEvent(
                    kind="model.completed",
                    status="completed",
                    target_type="model",
                    target_id=model_target_id,
                    duration_ms=(perf_counter() - started) * 1000,
                    details={
                        "iteration": iteration,
                        "finish_reason": result.finish_reason,
                        "tool_call_count": len(message.tool_calls),
                        "tool_calls": [
                            _tool_call_trace_details(call, self.context.capabilities) for call in message.tool_calls
                        ],
                        "content_length": len(message.text),
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                    },
                ),
            )
        return ChatResult(
            generations=[ChatGeneration(message=message)],
            llm_output={
                "token_usage": {
                    "prompt_tokens": result.input_tokens,
                    "completion_tokens": result.output_tokens,
                    "total_tokens": result.input_tokens + result.output_tokens,
                },
                "finish_reason": result.finish_reason,
            },
        )


def ai_message_result(message: AIMessage) -> LLMResult:
    usage = dict(message.usage_metadata or {})
    return LLMResult(
        message=_to_wire_ai_message(message),
        input_tokens=cast(int, usage.get("input_tokens") or 0),
        output_tokens=cast(int, usage.get("output_tokens") or 0),
        finish_reason=str(message.response_metadata.get("finish_reason") or "") or None,
    )


def _to_ai_message(result: LLMResult, context: ModelRunContext) -> AIMessage:
    raw = result.message
    content = raw.get("content") or ""
    tool_calls: list[dict[str, Any]] = []
    raw_tool_calls: list[dict[str, Any]] = []
    for index, call in enumerate(raw.get("tool_calls") or []):
        function = call.get("function") or {}
        call_id = str(call.get("id") or f"call_{uuid4().hex}_{index}")
        name = str(function.get("name") or "")
        arguments = function.get("arguments") or "{}"
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
            if not isinstance(parsed, dict):
                raise ValueError("arguments must decode to an object")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            context.invalid_tool_arguments[call_id] = f"Invalid JSON arguments: {exc}"
            parsed = {}
        tool_calls.append({"name": name, "args": parsed, "id": call_id, "type": "tool_call"})
        raw_tool_calls.append(
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
                },
            }
        )

    if not content and not tool_calls:
        content = "The model returned an empty response."
        context.empty_response = True

    return AIMessage(
        content=content,
        tool_calls=tool_calls,
        additional_kwargs={"tool_calls": raw_tool_calls} if raw_tool_calls else {},
        response_metadata={"finish_reason": result.finish_reason},
        usage_metadata={
            "input_tokens": result.input_tokens,
            "output_tokens": result.output_tokens,
            "total_tokens": result.input_tokens + result.output_tokens,
        },
    )


def _to_wire_ai_message(message: AIMessage) -> dict[str, Any]:
    raw_calls = message.additional_kwargs.get("tool_calls")
    if not raw_calls:
        raw_calls = [
            {
                "id": call["id"],
                "type": "function",
                "function": {"name": call["name"], "arguments": json.dumps(call["args"])},
            }
            for call in message.tool_calls
        ]
    wire: dict[str, Any] = {"role": "assistant", "content": message.text}
    if raw_calls:
        wire["tool_calls"] = raw_calls
    return wire


def _tool_choice_name(tool_choice: Any) -> str | None:
    if not isinstance(tool_choice, dict):
        return None
    function = tool_choice.get("function")
    return str(function.get("name")) if isinstance(function, dict) and function.get("name") else None


def _tool_call_trace_details(call: Mapping[str, Any], capabilities: dict[str, Any]) -> dict[str, Any]:
    name = str(call.get("name") or "")
    capability = capabilities.get(name)
    return {
        "call_id": call.get("id"),
        "function_name": name,
        "capability_id": capability.capability_id if capability else None,
        "capability_kind": capability.kind if capability else None,
        "arguments": sanitize_trace_data(call.get("args") or {}),
    }
