"""Bind domain adapters and pass native inputs to core.agent_runtime.factory.

Business middleware and callback dependencies are configured here, never
inside the runtime package.

Middleware composition (first = outermost):
  OrderedBatch → OutputGuard / ModelCallLimit / Summarization (model hooks)
  → ToolError → ToolRetry (tool hooks; retry inner so errors surface to formatter).
"""

from __future__ import annotations

from typing import Any, Sequence

from langchain.agents.middleware import ToolErrorMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.tools import BaseTool

from tianzhou_agent_platform.core.agent_runtime import build_agent
from tianzhou_agent_platform.core.agent_runtime.middleware.model_policy import OutputGuardMiddleware
from tianzhou_agent_platform.core.agent_runtime.middleware.tool_policy import (
    OrderedBatchMiddleware,
)
from tianzhou_agent_platform.observability.callbacks import ObservationCallbackHandler
from tianzhou_agent_platform.services.agent_integration.retries import (
    build_model_call_limit,
    build_tool_retry_middleware,
)
from tianzhou_agent_platform.services.agent_integration.tools import format_tool_error


async def format_tool_error_async(exc: Exception, request: Any) -> str | None:
    return format_tool_error(exc, request)


def build_chat_agent(
    *,
    model: BaseChatModel | str,
    tools: Sequence[BaseTool] = (),
    system_prompt: str | None = None,
    checkpointer: Any | None = None,
    middleware: Sequence[Any] = (),
    observation_sink: Any | None = None,
    use_tool_error_formatter: bool = True,
    use_output_guards: bool = True,
    use_ordered_batch: bool = True,
    use_model_call_limit: bool = True,
    model_run_limit: int | None = None,
    model_thread_limit: int | None = None,
    retry_eligible_tools: Sequence[str] = (),
    retry_max_attempts: int = 2,
    summarization_model: BaseChatModel | str | None = None,
    summarization_trigger: tuple[str, float | int] | None = None,
    summarization_keep: tuple[str, float | int] = ("messages", 6),
    allowed_tool_names: set[str] | None = None,
    hidden_tool_names: set[str] | None = None,
) -> Any:
    """Assemble the compiled native agent with Unibot business policies."""
    from tianzhou_agent_platform.services.agent_integration.context import (
        build_summarization_middleware,
    )
    from tianzhou_agent_platform.services.agent_integration.scope import filter_advertised_tools

    effective_tools = filter_advertised_tools(
        list(tools),
        allowed_names=allowed_tool_names,
        hidden_names=hidden_tool_names,
    )

    # Order matters: first entry is outermost.
    mw: list[Any] = []

    # Outermost tool wrapper: ordered batch + signature dedup (outside retries).
    if use_ordered_batch:
        mw.append(OrderedBatchMiddleware())

    # Model-loop guards and budget.
    if use_output_guards:
        mw.append(OutputGuardMiddleware())
    if use_model_call_limit and (model_run_limit is not None or model_thread_limit is not None):
        mw.append(
            build_model_call_limit(
                run_limit=model_run_limit,
                thread_limit=model_thread_limit,
                exit_behavior="end",
            )
        )
    if summarization_model is not None:
        mw.append(
            build_summarization_middleware(
                model=summarization_model,
                trigger=summarization_trigger,
                keep=summarization_keep,
            )
        )

    # Extra caller-supplied middleware (e.g. HITL) sits between model and tool policies.
    mw.extend(middleware)

    # Tool hooks: error formatter outer, retry inner (see ToolErrorMiddleware docs).
    if use_tool_error_formatter:
        mw.append(ToolErrorMiddleware(aon_error=format_tool_error_async))
    if retry_eligible_tools:
        mw.append(
            build_tool_retry_middleware(
                eligible_tools=retry_eligible_tools,
                max_retries=retry_max_attempts,
            )
        )

    agent = build_agent(
        model=model,
        tools=effective_tools,
        system_prompt=system_prompt,
        middleware=mw,
        checkpointer=checkpointer,
    )
    if observation_sink is not None:
        agent = with_callbacks(agent, [ObservationCallbackHandler(sink=observation_sink)])
    return agent


def with_callbacks(agent: Any, callbacks: list[Any]) -> Any:
    """Attach native callbacks through runnable configuration at invoke/stream time."""

    class _AgentWithCallbacks:
        def __init__(self, inner: Any, cbs: list[Any]) -> None:
            self._inner = inner
            self._callbacks = cbs

        def _merge(self, config: Any) -> Any:
            config = dict(config or {})
            existing = list(config.get("callbacks") or [])
            config["callbacks"] = [*existing, *self._callbacks]
            return config

        async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
            return await self._inner.ainvoke(input, config=self._merge(config), **kwargs)

        def invoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
            return self._inner.invoke(input, config=self._merge(config), **kwargs)

        def astream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
            return self._inner.astream(input, config=self._merge(config), **kwargs)

        def stream(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
            return self._inner.stream(input, config=self._merge(config), **kwargs)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    return _AgentWithCallbacks(agent, callbacks)
