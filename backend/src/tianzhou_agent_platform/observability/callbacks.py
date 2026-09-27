"""Native AsyncCallbackHandler forwarding execution events to the observation service.

Attach through runnable configuration for agent, summarizer, document-worker
and health calls. The runtime never constructs these callbacks.
"""

from __future__ import annotations

from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler


class ObservationCallbackHandler(AsyncCallbackHandler):
    """Forward model/tool lifecycle events to the observation service."""

    def __init__(self, sink: Any | None = None) -> None:
        self._sink = sink

    async def on_chat_model_start(self, serialized: Any, messages: Any, **kwargs: Any) -> None:
        if self._sink is not None and hasattr(self._sink, "on_model_start"):
            await self._sink.on_model_start(serialized=serialized, messages=messages, **kwargs)

    async def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        if self._sink is not None and hasattr(self._sink, "on_model_end"):
            await self._sink.on_model_end(response=response, **kwargs)

    async def on_llm_error(self, error: BaseException, **kwargs: Any) -> None:
        if self._sink is not None and hasattr(self._sink, "on_model_error"):
            await self._sink.on_model_error(error=error, **kwargs)

    async def on_llm_new_token(self, token: str, **kwargs: Any) -> None:
        if self._sink is not None and hasattr(self._sink, "on_token"):
            await self._sink.on_token(token=token, **kwargs)

    async def on_tool_start(self, serialized: Any, input_str: str, **kwargs: Any) -> None:
        if self._sink is not None and hasattr(self._sink, "on_tool_start"):
            await self._sink.on_tool_start(serialized=serialized, input_str=input_str, **kwargs)

    async def on_tool_end(self, output: Any, **kwargs: Any) -> None:
        if self._sink is not None and hasattr(self._sink, "on_tool_end"):
            await self._sink.on_tool_end(output=output, **kwargs)

    async def on_tool_error(self, error: BaseException, **kwargs: Any) -> None:
        if self._sink is not None and hasattr(self._sink, "on_tool_error"):
            await self._sink.on_tool_error(error=error, **kwargs)

    async def on_retry(self, retry_state: Any, **kwargs: Any) -> None:
        if self._sink is not None and hasattr(self._sink, "on_retry"):
            await self._sink.on_retry(retry_state=retry_state, **kwargs)
