"""Provider compatibility around the model call.

Which provider exceptions mean what is provider knowledge; it is injected, so the runtime depends on no SDK.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage
from langgraph.errors import GraphBubbleUp

from tianzhou_agent_platform.core.agent_runtime.middleware.model_policy import reject_truncated_call

_TOOL_CHOICE_MODES = frozenset({"auto", "any", "none", "required"})


class ProviderErrorMiddleware(AgentMiddleware):
    """Translate provider exceptions raised by the model call (``map_error`` returns None to leave one as is)."""

    def __init__(self, map_error: Callable[[Exception], Exception | None]) -> None:
        super().__init__()
        self.map_error = map_error

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            mapped = self.map_error(exc)
            if mapped is None:
                raise
            raise mapped from exc


class ForcedToolChoiceMiddleware(AgentMiddleware):
    """Provider compatibility for a forced (named) tool choice, and enforcement of the forced call.

    Several OpenAI-compatible providers reject a named ``tool_choice`` while streaming, and some (e.g. in thinking
    mode) reject it altogether; others accept it and answer in text anyway. A forced request is sent without
    streaming (it has no user-visible text). When the provider rejects the tool choice (``rejects_tool_choice``)
    or answers without calling the function, it is asked once more, instructed to call the function (and without
    a tool choice if it was rejected). If the call is still missing, the run ends with ``reply`` and ``failure``
    holds it, so the caller can fail the run instead of passing off an answer the selected capability never
    produced. Place it outside any per-request recorder so every request is recorded.
    """

    def __init__(self, rejects_tool_choice: Callable[[Exception], bool], *, reply: str) -> None:
        super().__init__()
        self.rejects_tool_choice = rejects_tool_choice
        self.reply = reply
        self.failure: str | None = None

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        name = request.tool_choice if isinstance(request.tool_choice, str) else None
        if name is None or name in _TOOL_CHOICE_MODES:
            return await handler(request)
        if isinstance(request.model, BaseChatModel) and not request.model.disable_streaming:
            request = request.override(model=request.model.model_copy(update={"disable_streaming": True}))
        response: ModelResponse | None
        try:
            response = await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            if not self.rejects_tool_choice(exc):
                raise
            request = request.override(tool_choice=None)
            response = None
        if response is None or not _settled(response, name):
            system = request.system_message.content if request.system_message is not None else ""
            system = system if isinstance(system, str) else str(system)
            directive = f"The caller explicitly selected function {name}. Call that function before answering the user."
            response = await handler(
                request.override(
                    system_message=SystemMessage(content=f"{system}\n\n{directive}" if system else directive)
                )
            )
        if _settled(response, name):
            return response
        self.failure = self.reply
        return ModelResponse(result=[AIMessage(content=self.reply)])


def _settled(response: ModelResponse, name: str) -> bool:
    """Whether the response calls ``name`` (arguments that failed to parse still count as the call).

    A truncated response is left to the output guard, which fails it with its own notice.
    """
    messages = [message for message in response.result if isinstance(message, AIMessage)]
    return any(
        reject_truncated_call(finish_reason=(message.response_metadata or {}).get("finish_reason"))
        or any(call.get("name") == name for call in [*message.tool_calls, *message.invalid_tool_calls])
        for message in messages
    )
