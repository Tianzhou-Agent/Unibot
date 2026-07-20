from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from langsmith import Client, trace, tracing_context
from langsmith.run_trees import RunTree
from pydantic import BaseModel

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.trace_details import sanitize_trace_data

logger = logging.getLogger(__name__)


class LangSmithObservability:
    def __init__(self, settings: AgentSettings) -> None:
        self.enabled = settings.langsmith_tracing
        self.project = settings.langsmith_project
        self._client: Client | None = None
        if not self.enabled:
            return
        api_key = settings.langsmith_api_key.get_secret_value() if settings.langsmith_api_key else None
        self._client = Client(
            api_url=settings.langsmith_endpoint,
            api_key=api_key,
            workspace_id=settings.langsmith_workspace_id,
            tracing_sampling_rate=settings.langsmith_sampling_rate,
            hide_inputs=_sanitize_langsmith_payload,
            hide_outputs=_sanitize_langsmith_payload,
            hide_metadata=_sanitize_langsmith_payload,
            tracing_error_callback=lambda exc: logger.warning("LangSmith trace export failed: %s", exc),
        )

    @asynccontextmanager
    async def run(
        self,
        name: str,
        *,
        inputs: dict[str, Any],
        metadata: dict[str, Any],
        tags: list[str] | None = None,
    ) -> AsyncIterator[RunTree | None]:
        if self._client is None:
            yield None
            return
        with tracing_context(
            enabled=True,
            client=self._client,
            project_name=self.project,
            metadata=_sanitize_langsmith_payload(metadata),
            tags=tags,
        ):
            with trace(
                name,
                inputs=_sanitize_langsmith_payload(inputs),
                project_name=self.project,
                metadata=_sanitize_langsmith_payload(metadata),
                tags=tags,
                client=self._client,
            ) as run:
                yield run

    async def close(self) -> None:
        if self._client is not None:
            await asyncio.to_thread(self._client.close, 5.0)


def _sanitize_langsmith_payload(value: Any) -> Any:
    return sanitize_trace_data(_normalize_trace_value(value))


def _normalize_trace_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 12:
        return f"<{type(value).__name__}>"
    if isinstance(value, BaseModel):
        return _normalize_trace_value(value.model_dump(mode="json"), depth=depth + 1)
    if isinstance(value, Mapping):
        return {
            str(key): _normalize_trace_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_normalize_trace_value(item, depth=depth + 1) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return f"<{type(value).__name__}>"
