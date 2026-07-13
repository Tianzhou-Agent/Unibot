from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, cast
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Request

from tianzhou_agent_platform.aina.builtin import ensure_unibot_assistant
from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.api.errors import install_exception_handlers
from tianzhou_agent_platform.api.router import create_router
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.agent import AgentRuntime
from tianzhou_agent_platform.core.llm import LLMClient, OpenAICompatibleClient
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.store.lifecycle import StorageStores, create_storage_stores
from tianzhou_agent_platform.store.repository import PersistentRepository, repository_tables
from tianzhou_agent_platform.store.runtime_check import (
    RUNTIME_CHECK_RESOURCE,
    run_storage_runtime_check,
    runtime_check_table,
)
from tianzhou_agent_platform.store.settings import StorageSettings


def create_app(
    *,
    settings: AgentSettings | None = None,
    repository: InMemoryRepository | None = None,
    storage_settings: StorageSettings | None = None,
    llm: LLMClient | None = None,
    capability_http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    resolved_settings = settings or AgentSettings()
    storage_stores: StorageStores | None = None
    resolved_repository: InMemoryRepository
    if repository is None and storage_settings is not None:
        storage_stores = create_storage_stores(
            storage_settings,
            mysql_resource_tables={
                **repository_tables,
                RUNTIME_CHECK_RESOURCE: runtime_check_table,
            },
        )
        resolved_repository = PersistentRepository(storage_stores)
    else:
        resolved_repository = repository or InMemoryRepository()
    resolved_llm = llm or OpenAICompatibleClient(resolved_settings)
    gateway = RemoteCapabilityGateway(resolved_settings, capability_http_client)

    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI) -> AsyncIterator[None]:
        try:
            if storage_stores is not None and storage_settings is not None:
                storage_settings.nas_root_path.mkdir(parents=True, exist_ok=True)
                await cast(PersistentRepository, resolved_repository).initialize()
                lifespan_app.state.storage_status = await run_storage_runtime_check(storage_stores)
            await ensure_unibot_assistant(resolved_repository)
            yield
        finally:
            background_tasks = cast(set[asyncio.Task[Any]], lifespan_app.state.background_tasks)
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
            await gateway.aclose()
            close = getattr(resolved_llm, "aclose", None)
            if close is not None:
                await close()
            if storage_stores is not None:
                await storage_stores.close()

    app = FastAPI(
        title="Tianzhou Agent Platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.repository = resolved_repository
    app.state.llm = resolved_llm
    app.state.capability_gateway = gateway
    app.state.storage_stores = storage_stores
    app.state.storage_status = None
    app.state.agent_runtime = AgentRuntime(
        settings=resolved_settings,
        repository=resolved_repository,
        llm=resolved_llm,
        gateway=gateway,
    )
    app.state.background_tasks = set()

    @app.middleware("http")
    async def attach_trace_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_trace_id = request.headers.get("X-Trace-ID") or f"request_{uuid4().hex}"
        response = await call_next(request)
        response.headers.setdefault("X-Trace-ID", request.state.request_trace_id)
        return response

    install_exception_handlers(app)
    app.include_router(create_router())

    return app


app = create_app(storage_settings=StorageSettings())


def run() -> None:
    uvicorn.run("tianzhou_agent_platform.main:app", host="0.0.0.0", port=8000, reload=False)
