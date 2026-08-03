from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, cast
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Request

from tianzhou_agent_platform.aina.builtin import ensure_builtin_ainas
from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.aina.document.task_service import DocumentEditTaskService, DocumentEditWorker
from tianzhou_agent_platform.aina.gateway import RemoteCapabilityGateway
from tianzhou_agent_platform.aina.managed import ManagedAinaRuntime
from tianzhou_agent_platform.aina.project_service import (
    AinaProjectArtifactStore,
    AinaProjectService,
    InMemoryAinaProjectArtifactStore,
    NasAinaProjectArtifactStore,
)
from tianzhou_agent_platform.aina.scheduler import AinaScheduler
from tianzhou_agent_platform.api.errors import install_exception_handlers
from tianzhou_agent_platform.api.router import create_router
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.agent import AgentRuntime
from tianzhou_agent_platform.core.llm import LLMClient, OpenAICompatibleClient
from tianzhou_agent_platform.core.observability import ObservabilityAspect
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.store.lifecycle import StorageStores, create_storage_stores
from tianzhou_agent_platform.store.repository import PersistentRepository, repository_tables
from tianzhou_agent_platform.store.runtime_check import (
    RUNTIME_CHECK_RESOURCE,
    run_storage_runtime_check,
    runtime_check_table,
)
from tianzhou_agent_platform.store.settings import StorageSettings
from tianzhou_agent_platform.sandbox.factory import create_sandbox_service
from tianzhou_agent_platform.sandbox.service import SandboxService
from tianzhou_agent_platform.vision.client import VisionClient


def create_app(
    *,
    settings: AgentSettings | None = None,
    repository: InMemoryRepository | None = None,
    storage_settings: StorageSettings | None = None,
    llm: LLMClient | None = None,
    capability_http_client: httpx.AsyncClient | None = None,
    model_health_http_client: httpx.AsyncClient | None = None,
    document_service: DocumentService | None = None,
    aina_project_artifact_store: AinaProjectArtifactStore | None = None,
    sandbox_service: SandboxService | None = None,
    vision_http_client: httpx.AsyncClient | None = None,
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
    resolved_document_service = document_service or (
        DocumentService(storage_stores.nas) if storage_stores is not None else None
    )
    resolved_aina_project_artifact_store = aina_project_artifact_store or (
        NasAinaProjectArtifactStore(storage_stores.nas)
        if storage_stores is not None
        else InMemoryAinaProjectArtifactStore()
    )
    aina_project_service = AinaProjectService(resolved_repository, resolved_aina_project_artifact_store)
    resolved_sandbox_service = sandbox_service or create_sandbox_service(
        resolved_settings,
        resolved_repository,
    )
    observability = ObservabilityAspect(resolved_repository)
    managed_aina_runtime = ManagedAinaRuntime(
        resolved_settings,
        resolved_repository,
        aina_project_service,
        resolved_sandbox_service,
    )
    resolved_llm = llm or OpenAICompatibleClient(
        resolved_settings,
        call_sink=observability.record_llm_call,
    )
    document_edit_task_service = (
        DocumentEditTaskService(resolved_document_service, resolved_repository, resolved_llm)
        if resolved_document_service is not None
        else None
    )
    document_edit_worker = DocumentEditWorker(document_edit_task_service) if document_edit_task_service else None
    gateway = RemoteCapabilityGateway(
        resolved_settings,
        capability_http_client,
        managed_runtime=managed_aina_runtime,
    )
    health_client = model_health_http_client or httpx.AsyncClient()
    scheduler = AinaScheduler(resolved_repository, gateway, node_id=resolved_settings.node_id)
    vision_client = VisionClient(
        base_url=resolved_settings.vision_base_url,
        timeout_seconds=resolved_settings.vision_timeout_seconds,
        http_client=vision_http_client,
    )

    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI) -> AsyncIterator[None]:
        try:
            if storage_stores is not None and storage_settings is not None:
                storage_settings.nas_root_path.mkdir(parents=True, exist_ok=True)
                await cast(PersistentRepository, resolved_repository).initialize()
                lifespan_app.state.storage_status = await run_storage_runtime_check(storage_stores)
            await ensure_builtin_ainas(
                resolved_repository,
                document_enabled=resolved_document_service is not None,
            )
            scheduler_task = asyncio.create_task(scheduler.run())
            lifespan_app.state.background_tasks.add(scheduler_task)
            if document_edit_worker is not None:
                document_worker_task = asyncio.create_task(document_edit_worker.run())
                lifespan_app.state.background_tasks.add(document_worker_task)
            yield
        finally:
            scheduler.stop()
            if document_edit_worker is not None:
                document_edit_worker.stop()
            background_tasks = cast(set[asyncio.Task[Any]], lifespan_app.state.background_tasks)
            if background_tasks:
                await asyncio.gather(*background_tasks, return_exceptions=True)
            await gateway.aclose()
            await resolved_sandbox_service.aclose()
            await vision_client.aclose()
            if model_health_http_client is None:
                await health_client.aclose()
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
    app.state.model_health_http_client = health_client
    app.state.document_service = resolved_document_service
    app.state.aina_project_service = aina_project_service
    app.state.managed_aina_runtime = managed_aina_runtime
    app.state.document_edit_task_service = document_edit_task_service
    app.state.document_edit_worker = document_edit_worker
    app.state.storage_stores = storage_stores
    app.state.storage_status = None
    app.state.observability = observability
    app.state.agent_runtime = AgentRuntime(
        settings=resolved_settings,
        repository=resolved_repository,
        llm=resolved_llm,
        gateway=gateway,
        observability=observability,
        document_service=resolved_document_service,
        document_edit_task_service=document_edit_task_service,
        sandbox_service=resolved_sandbox_service,
    )
    app.state.background_tasks = set()
    app.state.aina_scheduler = scheduler
    app.state.sandbox_service = resolved_sandbox_service
    app.state.vision_client = vision_client

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
