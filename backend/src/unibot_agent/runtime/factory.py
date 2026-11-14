"""Native agent factory.

Thin wrapper around langchain.agents.create_agent. Receives native chat
models, tools, middleware, callbacks, checkpointer and state/context schemas.
Does not accept ChatRequest, load Conversation records, or emit HTTP SSE.
"""

from __future__ import annotations

from typing import Any, Sequence

from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool


def build_agent(
    *,
    model: BaseChatModel | str,
    tools: Sequence[BaseTool] | None = None,
    system_prompt: str | None = None,
    middleware: Sequence[Any] = (),
    state_schema: type | None = None,
    context_schema: type | None = None,
    checkpointer: Any | None = None,
    response_format: Any | None = None,
    name: str | None = None,
) -> Any:
    """Assemble the compiled native agent. Business policies arrive via middleware."""
    return create_agent(
        model,
        tools=list(tools or ()),
        system_prompt=system_prompt,
        middleware=list(middleware),
        state_schema=state_schema,
        context_schema=context_schema,
        checkpointer=checkpointer,
        response_format=response_format,
        name=name,
    )
