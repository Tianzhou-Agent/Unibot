"""Observation of every model request the native agent loop makes.

Wraps the model call (inside the scope middleware, so the recorded request is exactly what the provider
receives) and publishes the trace contract the legacy LLM port produced: ``model.requested`` /
``model.completed`` / ``model.failed`` events, a ``model`` span, a persisted ``/llm-calls`` record with the
redacted request and response, and the run's accumulated token usage.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any, Awaitable, Callable
from uuid import uuid4

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage
from langchain_core.utils.function_calling import convert_to_openai_tool

from tianzhou_agent_platform.observability.models import LLMCallRecord
from tianzhou_agent_platform.observability.trace_details import redact_trace_data


@dataclass
class RunUsage:
    """Token usage of the model calls in one run."""

    input_tokens: int = 0
    output_tokens: int = 0
    estimated: bool = False


class ModelCallRecorder(AgentMiddleware):
    def __init__(
        self,
        *,
        events: Any,
        trace_id: str,
        conversation_id: str,
        model: str,
        endpoint: str,
        to_wire: Callable[[Any], dict[str, Any] | None],
        visible_capabilities: Callable[[], dict[str, Any]],
        call_sink: Callable[[LLMCallRecord], Awaitable[None]] | None = None,
        streaming: bool = False,
    ) -> None:
        super().__init__()
        self._events = events
        self._trace_id = trace_id
        self._conversation_id = conversation_id
        self._model = model
        self._endpoint = endpoint
        self._to_wire = to_wire
        self._visible = visible_capabilities
        self._call_sink = call_sink
        self._streaming = streaming
        self.iterations = 0
        self.usage = RunUsage()

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        self.iterations += 1
        iteration = self.iterations
        capabilities = self._visible()
        messages = self._wire_messages(request)
        tools = [convert_to_openai_tool(tool) for tool in request.tools]
        tool_choice = _wire_tool_choice(request.tool_choice)
        span_id = f"span_{uuid4().hex}"
        await self._events.push(
            self._trace_id,
            kind="model.requested",
            status="started",
            conversation_id=self._conversation_id,
            target_type="model",
            target_id=self._model,
            details={
                "iteration": iteration,
                "message_count": len(messages),
                "message_roles": [str(message.get("role") or "unknown") for message in messages],
                "capability_ids": sorted({capability.capability_id for capability in capabilities.values()}),
                "forced_function": _forced_function(tool_choice),
                "streaming": self._streaming,
            },
        )
        await self._events.start_span(
            self._trace_id,
            span_id=span_id,
            kind="model",
            name="model.complete",
            target_id=self._model,
            input_data={
                "messages": [_message_preview(message) for message in messages],
                "tools": tools,
                "tool_choice": tool_choice,
            },
            attributes={"streaming": self._streaming, "context_type": "conversation"},
        )
        call = LLMCallRecord(
            call_id=f"llm_{uuid4().hex}",
            trace_id=self._trace_id,
            span_id=span_id,
            context_type="conversation",
            context_id=self._conversation_id,
            endpoint=self._endpoint,
            model=self._model,
            request=redact_trace_data(
                {
                    "model": self._model,
                    "messages": messages,
                    "stream": self._streaming,
                    **({"tools": tools} if tools else {}),
                    **({"tool_choice": tool_choice} if tool_choice is not None else {}),
                }
            ),
        )
        await self._record(call)
        started = perf_counter()
        try:
            response = await handler(request)
        except Exception as exc:
            duration_ms = (perf_counter() - started) * 1000
            error = _error_details(exc)
            await self._events.finish_span(self._trace_id, span_id, "failed", error=error)
            await self._events.push(
                self._trace_id,
                kind="model.failed",
                status="failed",
                conversation_id=self._conversation_id,
                target_type="model",
                target_id=self._model,
                duration_ms=duration_ms,
                details={"iteration": iteration, **error},
            )
            await self._record(
                call.model_copy(
                    update={
                        "status": "failed",
                        "response": redact_trace_data({"error": error}),
                        "duration_ms": duration_ms,
                        "error": redact_trace_data(str(exc)),
                        "completed_at": datetime.now(UTC),
                    }
                )
            )
            raise

        duration_ms = (perf_counter() - started) * 1000
        message = next((item for item in reversed(response.result) if isinstance(item, AIMessage)), None)
        wire_message = (self._to_wire(message) if message is not None else None) or {"role": "assistant"}
        input_tokens, output_tokens, estimated = _usage(message)
        self.usage.input_tokens += input_tokens
        self.usage.output_tokens += output_tokens
        self.usage.estimated = self.usage.estimated or estimated
        finish_reason = (message.response_metadata or {}).get("finish_reason") if message is not None else None
        tool_calls = [call for call in wire_message.get("tool_calls") or [] if isinstance(call, dict)]
        content_length = len(str(wire_message.get("content") or ""))
        await self._events.finish_span(
            self._trace_id,
            span_id,
            "completed",
            output_data=_message_preview(wire_message),
            attributes={
                "finish_reason": finish_reason,
                "tool_call_count": len(tool_calls),
                "content_length": content_length,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "usage_estimated": estimated,
            },
        )
        await self._events.push(
            self._trace_id,
            kind="model.completed",
            status="completed",
            conversation_id=self._conversation_id,
            target_type="model",
            target_id=self._model,
            duration_ms=duration_ms,
            details={
                "iteration": iteration,
                "finish_reason": finish_reason,
                "tool_call_count": len(tool_calls),
                "tool_calls": [_tool_call_details(call, capabilities) for call in tool_calls],
                "content_length": content_length,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "usage_estimated": estimated,
            },
        )
        usage: dict[str, Any] = {
            "prompt_tokens": input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
        if estimated:
            usage.update({"estimated": True, "source": "estimated"})
        await self._record(
            call.model_copy(
                update={
                    "status": "completed",
                    "response": redact_trace_data(
                        {
                            "object": "chat.completion",
                            "model": self._model,
                            "choices": [{"index": 0, "message": wire_message, "finish_reason": finish_reason}],
                            "usage": usage,
                        }
                    ),
                    "duration_ms": duration_ms,
                    "completed_at": datetime.now(UTC),
                }
            )
        )
        return response

    def _wire_messages(self, request: ModelRequest) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        if request.system_message is not None:
            messages.append({"role": "system", "content": _text(request.system_message.content)})
        for message in request.messages:
            record = self._to_wire(message)
            if record is not None:
                messages.append(record)
        return messages

    async def _record(self, call: LLMCallRecord) -> None:
        if self._call_sink is None:
            return
        try:
            await self._call_sink(call)
        except Exception:  # noqa: BLE001 - observation must not fail the run
            return


async def record_standalone_call(
    call_sink: Callable[[LLMCallRecord], Awaitable[None]] | None,
    *,
    trace_id: str,
    span_id: str | None,
    context_type: str,
    context_id: str,
    model: str,
    endpoint: str,
    messages: list[dict[str, Any]],
    response: AIMessage | None,
    duration_ms: float,
    error: Exception | None = None,
) -> tuple[int, int]:
    """Persist the /llm-calls record of a model call made outside the agent loop (e.g. context compression).

    Returns the call's (input, output) token usage.
    """
    input_tokens, output_tokens, estimated = _usage(response)
    if call_sink is None:
        return input_tokens, output_tokens
    finish_reason = (response.response_metadata or {}).get("finish_reason") if response is not None else None
    message = {"role": "assistant", "content": _text(response.content)} if response is not None else None
    usage: dict[str, Any] = {
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    if estimated:
        usage.update({"estimated": True, "source": "estimated"})
    record = LLMCallRecord(
        call_id=f"llm_{uuid4().hex}",
        trace_id=trace_id,
        span_id=span_id,
        context_type=context_type,
        context_id=context_id,
        endpoint=endpoint,
        model=model,
        status="failed" if error is not None else "completed",
        request=redact_trace_data({"model": model, "messages": messages, "stream": False}),
        response=redact_trace_data(
            {"error": _error_details(error)}
            if error is not None
            else {
                "object": "chat.completion",
                "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": usage,
            }
        ),
        duration_ms=duration_ms,
        error=redact_trace_data(str(error)) if error is not None else None,
        completed_at=datetime.now(UTC),
    )
    try:
        await call_sink(record)
    except Exception:  # noqa: BLE001 - observation must not fail the caller
        pass
    return input_tokens, output_tokens


def _usage(message: AIMessage | None) -> tuple[int, int, bool]:
    metadata = getattr(message, "usage_metadata", None) or {}
    if metadata:
        return int(metadata.get("input_tokens") or 0), int(metadata.get("output_tokens") or 0), False
    return 0, 0, message is not None


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return json.dumps(content, ensure_ascii=False, default=str)


def _wire_tool_choice(tool_choice: Any) -> Any:
    if isinstance(tool_choice, str) and tool_choice not in {"auto", "any", "none", "required"}:
        return {"type": "function", "function": {"name": tool_choice}}
    return tool_choice


def _forced_function(tool_choice: Any) -> str | None:
    if not isinstance(tool_choice, dict):
        return None
    function = tool_choice.get("function")
    return str(function.get("name")) if isinstance(function, dict) and function.get("name") else None


def _message_preview(message: dict[str, Any]) -> dict[str, Any]:
    preview = dict(message)
    tool_calls = message.get("tool_calls")
    if isinstance(tool_calls, list):
        preview["tool_calls"] = [_tool_call_preview(call) for call in tool_calls if isinstance(call, dict)]
    return preview


def _tool_call_preview(call: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function")
    if not isinstance(function, dict):
        return dict(call)
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except (TypeError, ValueError):
            pass
    return {"call_id": call.get("id"), "function_name": function.get("name"), "arguments": arguments}


def _tool_call_details(call: dict[str, Any], capabilities: dict[str, Any]) -> dict[str, Any]:
    function = call.get("function") or {}
    function_name = str(function.get("name") or "")
    capability = capabilities.get(function_name)
    return {
        "call_id": str(call.get("id") or ""),
        "function_name": function_name,
        "capability_id": capability.capability_id if capability else None,
        "kind": capability.kind if capability else None,
        "arguments": _tool_call_preview(call).get("arguments"),
    }


def _error_details(exc: Exception) -> dict[str, Any]:
    code = getattr(exc, "code", None)
    if code is not None:
        return {"code": code, "message": getattr(exc, "message", str(exc)), "retryable": getattr(exc, "retryable", False)}
    return {"type": type(exc).__name__, "message": str(exc)}
