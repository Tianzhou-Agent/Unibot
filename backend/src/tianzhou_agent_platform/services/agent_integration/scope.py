"""Capability scope of an agent run: what each model request advertises and how out-of-scope calls are answered.

Every capability reachable in a run is registered as a tool; the scope decides which ones the next model request
advertises, with which system prompt. Activating an AINA scope changes the following model request; sibling calls
of the same response stay governed by the scope they were proposed in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Sequence

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool


@dataclass
class RunScope:
    """Mutable capability scope of one agent run.

    Every reachable capability is registered with the agent up front; this object decides which of them the
    next model request advertises and which system prompt it carries. Tool executors update it when an AINA
    scope is activated, so the switch takes effect on the following model request.
    """

    visible: dict[str, Any]
    system_prompt: str
    widgets: list[Any] = field(default_factory=list)
    activated_in_batch: bool = False
    # Set when a tool completes the turn itself (e.g. a document edit task was created); the next model
    # request returns this text instead of calling the model.
    direct_reply: str | None = None
    # Capability the caller forced (API ``capability``); applied as tool_choice on the first model request only.
    forced_function: str | None = None
    # Builds the model-visible error for a call to a name no registered tool has (e.g. a capability id or a
    # tool owned by an inactive AINA scope): ``(name, call_id) -> tool message content``.
    on_unknown_tool: Callable[[str, str], Awaitable[str]] | None = None
    # Rewrites the transcript for the model request given the visible names (historical calls outside the
    # scope become non-executable context); ``(messages, visible_names) -> messages``.
    project_messages: Callable[[list[Any], set[str]], list[Any]] | None = None
    # AINA entry functions activated during this run: their calls stay ordinary context for the rest of the
    # run instead of being projected as out-of-scope history (the model otherwise echoes the history note).
    activated_entries: set[str] = field(default_factory=set)
    # Records a failed tool result the executor never saw (e.g. a call blocked by the run's attempt budget):
    # ``(name, call_id, error_payload)``. Executors add the call ids they already recorded to
    # ``recorded_failures`` so no failure is recorded twice.
    on_tool_error: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None
    recorded_failures: set[str] = field(default_factory=set)
    # Per-request context appended to the scope prompt (e.g. the current task projection), refreshed before
    # every model call so tool effects earlier in the run are visible.
    prompt_suffix: Callable[[], Awaitable[str]] | None = None
    # Transient UI context of this turn: shown to the model inside the user message with this id, never stored.
    ui_context: str | None = None
    ui_context_message_id: str | None = None


class CapabilityScopeMiddleware(AgentMiddleware):
    """Advertise only the run's visible capabilities and the scope's system prompt on every model call."""

    def __init__(self, scope: RunScope) -> None:
        super().__init__()
        self.scope = scope

    def _scoped(self, request: ModelRequest, suffix: str = "") -> ModelRequest:
        self.scope.activated_in_batch = False  # a new model response starts a new batch
        tools = filter_advertised_tools(request.tools, allowed_names=set(self.scope.visible))
        prompt = f"{self.scope.system_prompt}\n\n{suffix}" if suffix else self.scope.system_prompt
        overrides: dict[str, Any] = {
            "tools": tools,
            "system_message": SystemMessage(content=prompt),
        }
        messages = list(request.messages)
        if self.scope.ui_context:
            messages = [_with_ui_context(message, self.scope) for message in messages]
        if self.scope.project_messages is not None:
            messages = self.scope.project_messages(messages, set(self.scope.visible) | self.scope.activated_entries)
        overrides["messages"] = messages
        forced, self.scope.forced_function = self.scope.forced_function, None
        if forced is not None and forced in self.scope.visible:
            overrides["tool_choice"] = forced
        return request.override(**overrides)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        if self.scope.direct_reply is not None:
            reply, self.scope.direct_reply = self.scope.direct_reply, None
            return ModelResponse(result=[AIMessage(content=reply)])
        suffix = await self.scope.prompt_suffix() if self.scope.prompt_suffix is not None else ""
        return await handler(self._scoped(request, suffix))

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        if self.scope.direct_reply is not None:
            reply, self.scope.direct_reply = self.scope.direct_reply, None
            return ModelResponse(result=[AIMessage(content=reply)])
        return handler(self._scoped(request))

    async def awrap_tool_call(self, request: Any, handler: Callable[[Any], Awaitable[Any]]) -> Any:
        from tianzhou_agent_platform.services.agent_integration.capability_tools import current_tool_call_id

        call_id = str(request.tool_call.get("id") or "")
        if request.tool is None and self.scope.on_unknown_tool is not None:
            name = str(request.tool_call.get("name") or "")
            return ToolMessage(
                content=await self.scope.on_unknown_tool(name, call_id),
                tool_call_id=call_id,
                name=name,
                status="error",
            )
        token = current_tool_call_id.set(call_id or None)
        try:
            result = await handler(request)
        finally:
            current_tool_call_id.reset(token)
        payload = _error_payload(result)
        if payload is not None and call_id not in self.scope.recorded_failures and self.scope.on_tool_error:
            await self.scope.on_tool_error(str(request.tool_call.get("name") or ""), call_id, payload)
        return result


def _with_ui_context(message: Any, scope: RunScope) -> Any:
    if not isinstance(message, HumanMessage) or message.id != scope.ui_context_message_id:
        return message
    content = message.content if isinstance(message.content, str) else str(message.content)
    return message.model_copy(update={"content": f"{content}\n\n<ui_context>\n{scope.ui_context}\n</ui_context>"})


def _error_payload(result: Any) -> dict[str, Any] | None:
    content = getattr(result, "content", None)
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except ValueError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return payload
    return None


def filter_advertised_tools(
    tools: Sequence[BaseTool],
    *,
    allowed_names: set[str] | None = None,
    hidden_names: set[str] | None = None,
) -> list[BaseTool]:
    """Deterministic scope filtering of tools advertised to the model.

    Registration alone does not grant execution permission; hidden tools and
    skill instructions must not leak into the prompt.
    """
    allowed = allowed_names
    hidden = hidden_names or set()
    result: list[BaseTool] = []
    for tool in tools:
        name = tool.name
        if name in hidden:
            continue
        if allowed is not None and name not in allowed:
            continue
        result.append(tool)
    return result
