"""Provider compatibility around the model call.

Which provider exceptions mean what is provider knowledge; it is injected, so the runtime depends on no SDK.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.errors import GraphBubbleUp

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
    """Provider compatibility for a forced (named) tool choice.

    Several OpenAI-compatible providers reject a named ``tool_choice`` while streaming, and some (e.g. in thinking
    mode) reject it altogether. A forced request is sent without streaming (it has no user-visible text), and if
    the provider rejects the tool choice (``rejects_tool_choice``) it is sent once more without one, instructed to
    call the function. Place it outside any per-request recorder so both requests are recorded.
    """

    def __init__(self, rejects_tool_choice: Callable[[Exception], bool]) -> None:
        super().__init__()
        self.rejects_tool_choice = rejects_tool_choice

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
        try:
            return await handler(request)
        except GraphBubbleUp:
            raise
        except Exception as exc:
            if not self.rejects_tool_choice(exc):
                raise
        system = request.system_message.content if request.system_message is not None else ""
        system = system if isinstance(system, str) else str(system)
        directive = f"The caller explicitly selected function {name}. Call that function before answering the user."
        return await handler(
            request.override(
                tool_choice=None,
                system_message=SystemMessage(content=f"{system}\n\n{directive}" if system else directive),
            )
        )
