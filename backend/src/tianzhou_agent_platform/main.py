from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Request

from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.api.errors import install_exception_handlers
from tianzhou_agent_platform.api.router import create_router
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.agent import AgentRuntime
from tianzhou_agent_platform.core.llm import LLMClient, OpenAICompatibleClient
from tianzhou_agent_platform.core.repository import InMemoryRepository


def create_app(
    *,
    settings: AgentSettings | None = None,
    repository: InMemoryRepository | None = None,
    llm: LLMClient | None = None,
    capability_http_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    resolved_settings = settings or AgentSettings()
    resolved_repository = repository or InMemoryRepository()
    resolved_llm = llm or OpenAICompatibleClient(resolved_settings)
    gateway = RemoteCapabilityGateway(resolved_settings, capability_http_client)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        await gateway.aclose()
        close = getattr(resolved_llm, "aclose", None)
        if close is not None:
            await close()

    app = FastAPI(
        title="Tianzhou Agent Platform",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = resolved_settings
    app.state.repository = resolved_repository
    app.state.llm = resolved_llm
    app.state.capability_gateway = gateway
    app.state.agent_runtime = AgentRuntime(
        settings=resolved_settings,
        repository=resolved_repository,
        llm=resolved_llm,
        gateway=gateway,
    )

    @app.middleware("http")
    async def attach_trace_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_trace_id = request.headers.get("X-Trace-ID") or f"request_{uuid4().hex}"
        response = await call_next(request)
        response.headers.setdefault("X-Trace-ID", request.state.request_trace_id)
        return response

    install_exception_handlers(app)
    app.include_router(create_router())

    return app


app = create_app()


def run() -> None:
    uvicorn.run("tianzhou_agent_platform.main:app", host="0.0.0.0", port=8000, reload=False)
