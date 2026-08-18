from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Awaitable, cast
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

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
from tianzhou_agent_platform.api.auth import SESSION_COOKIE
from tianzhou_agent_platform.api.dependencies import RequestActor
from tianzhou_agent_platform.api.router import create_router
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.llm import LLMClient, OpenAICompatibleClient
from tianzhou_agent_platform.core.observation_interceptors import ObservedAgentRuntime
from tianzhou_agent_platform.core.observability import ObservabilityAspect
from tianzhou_agent_platform.core.observability_query import ObsQueryService
from tianzhou_agent_platform.core.observability_writer import ObsIngestWorker
from tianzhou_agent_platform.core.observation_logging import ObservationLogHandler
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.core.telemetry import DurableWalSpanProcessor, setup_tracer_provider, shutdown_tracer_provider
from tianzhou_agent_platform.store.lifecycle import StorageStores, create_storage_stores
from tianzhou_agent_platform.store.observability_raw import RawIoWriter
from tianzhou_agent_platform.store.observability_store import ObservabilityStore
from tianzhou_agent_platform.store.observability_wal import WalWriter, build_producer_instance_id
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
from tianzhou_agent_platform.auth.service import AuthService
from tianzhou_agent_platform.tasks.service import TaskEventBroker, TaskService
from tianzhou_agent_platform.tasks.store import InMemorySessionTaskStore, MySqlSessionTaskStore


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
    github_auth_http_client: httpx.AsyncClient | None = None,
    enforce_auth: bool = False,
) -> FastAPI:
    resolved_settings = settings or AgentSettings()
    storage_stores: StorageStores | None = None
    resolved_repository: InMemoryRepository

    # OBS pipeline: dedicated MySQL pool, WAL and raw IO (design sections 10/11)
    obs_store: ObservabilityStore | None = None
    obs_wal_writer: WalWriter | None = None
    obs_ingest_worker: ObsIngestWorker | None = None
    obs_query_service: ObsQueryService | None = None
    raw_io_writer: RawIoWriter | None = None
    if resolved_settings.obs_enabled and storage_settings is not None:
        obs_store = ObservabilityStore.from_dsn(storage_settings.mysql_dsn.get_secret_value())
        raw_io_writer = RawIoWriter(
            resolved_settings.obs_raw_root,
            max_file_size_bytes=storage_settings.nas_max_file_size_bytes,
        )
        obs_wal_writer = WalWriter(
            resolved_settings.obs_wal_root,
            build_producer_instance_id(resolved_settings.node_id),
            max_segment_bytes=32 * 1024 * 1024,
            rotation_interval_seconds=30.0,
        )
        obs_ingest_worker = ObsIngestWorker(
            resolved_settings.obs_wal_root,
            obs_store,
            obs_wal_writer.producer_instance_id,
            wal_max_bytes=resolved_settings.obs_wal_max_bytes,
            retention_days=resolved_settings.obs_retention_days,
            raw_root=resolved_settings.obs_raw_root,
        )
        obs_wal_writer.on_records_flushed = obs_ingest_worker.on_records_flushed
        obs_query_service = ObsQueryService(obs_store, resolved_settings.obs_raw_root)

    if repository is None and storage_settings is not None:
        storage_stores = create_storage_stores(
            storage_settings,
            mysql_resource_tables={
                **repository_tables,
                RUNTIME_CHECK_RESOURCE: runtime_check_table,
            },
        )
        resolved_repository = PersistentRepository(
            storage_stores,
            # Disabling the OBS pipeline is also the rollback path: keep the
            # legacy Trace/LLMCall tables writable and restart-recoverable.
            persist_observability=not resolved_settings.obs_enabled,
            obs_trace_status_resolver=(
                (lambda trace_id: _resolve_obs_trace_status(obs_store, trace_id))
                if obs_store is not None
                else None
            ),
        )
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
    tracer_provider = (
        setup_tracer_provider(
            DurableWalSpanProcessor(obs_wal_writer),
            service_instance_id=obs_wal_writer.producer_instance_id,
        )
        if obs_wal_writer is not None
        else None
    )
    observability = ObservabilityAspect(
        resolved_repository,
        wal_writer=obs_wal_writer,
        raw_io_writer=raw_io_writer,
        # design 14 fallback: direct OBS MySQL write when the WAL is down
        obs_store=obs_store,
        # P0 fix: the aspect needs a Tracer (start_span), not a TracerProvider.
        tracer=(tracer_provider.get_tracer("unibot") if tracer_provider is not None else None),
    )
    obs_log_handler = (
        ObservationLogHandler(obs_wal_writer) if obs_wal_writer is not None else None
    )
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
    auth_service = AuthService(
        settings=resolved_settings,
        repository=resolved_repository,
        github_http_client=github_auth_http_client,
    )
    task_store = (
        MySqlSessionTaskStore(storage_stores.mysql, storage_stores.redis)
        if storage_stores is not None
        else InMemorySessionTaskStore()
    )
    task_service = TaskService(
        resolved_repository,
        task_store,
        event_broker=TaskEventBroker(storage_stores.redis if storage_stores is not None else None),
        verification_timeout_seconds=resolved_settings.capability_timeout_seconds,
    )

    @asynccontextmanager
    async def lifespan(lifespan_app: FastAPI) -> AsyncIterator[None]:
        try:
            if storage_stores is not None and storage_settings is not None:
                storage_settings.nas_root_path.mkdir(parents=True, exist_ok=True)
                await cast(PersistentRepository, resolved_repository).initialize()
                lifespan_app.state.storage_status = await run_storage_runtime_check(storage_stores)
            await task_service.initialize()
            if obs_wal_writer is not None and obs_store is not None:
                # startup order (design 16.1): NAS roots -> OBS tables -> WAL -> worker
                resolved_settings.obs_wal_root.mkdir(parents=True, exist_ok=True)
                resolved_settings.obs_raw_root.mkdir(parents=True, exist_ok=True)
                await obs_store.create_tables()
                obs_wal_writer.start()
                if obs_log_handler is not None:
                    logging.getLogger().addHandler(obs_log_handler)
                if obs_ingest_worker is not None:
                    obs_ingest_worker.start()
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
            # OBS shutdown order (design 16.2): stop records -> drain+seal WAL
            # -> ingest remaining -> close the dedicated pool last.
            if obs_log_handler is not None:
                logging.getLogger().removeHandler(obs_log_handler)
            if obs_wal_writer is not None:
                obs_wal_writer.close()
                await obs_wal_writer.wait_closed()
            if obs_ingest_worker is not None:
                await obs_ingest_worker.stop()
            if obs_wal_writer is not None:
                shutdown_tracer_provider()
            if obs_store is not None:
                await obs_store.close()
            await gateway.aclose()
            await resolved_sandbox_service.aclose()
            await vision_client.aclose()
            await auth_service.aclose()
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
    app.state.obs_store = obs_store
    app.state.obs_ingest_worker = obs_ingest_worker
    app.state.obs_wal_writer = obs_wal_writer
    app.state.obs_log_handler = obs_log_handler
    app.state.obs_query = obs_query_service or ObsQueryService(None, None)
    app.state.agent_runtime = ObservedAgentRuntime(
        settings=resolved_settings,
        repository=resolved_repository,
        llm=resolved_llm,
        gateway=gateway,
        observability=observability,
        document_service=resolved_document_service,
        document_edit_task_service=document_edit_task_service,
        sandbox_service=resolved_sandbox_service,
        task_service=task_service,
    )
    app.state.background_tasks = set()
    app.state.aina_scheduler = scheduler
    app.state.sandbox_service = resolved_sandbox_service
    app.state.vision_client = vision_client
    app.state.auth_service = auth_service
    app.state.task_service = task_service
    app.state.auth_enforced = enforce_auth

    @app.middleware("http")
    async def attach_request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        request.state.request_trace_id = request.headers.get("X-Trace-ID") or f"request_{uuid4().hex}"
        user = await auth_service.resolve_session(request.cookies.get(SESSION_COOKIE))
        if user is not None:
            request.state.user = user
            request.state.actor = RequestActor(user_id=user.id, tenant_id=user.tenant_id)
        elif enforce_auth and not _is_public_path(request.url.path):
            trace_id = request.state.request_trace_id
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "code": "AUTHENTICATION_REQUIRED",
                        "message": "Authentication is required",
                        "retryable": False,
                        "source": "auth",
                        "user_message": "请先登录。",
                        "trace_id": trace_id,
                    }
                },
                headers={"X-Trace-ID": trace_id},
            )
        response = await call_next(request)
        response.headers.setdefault("X-Trace-ID", request.state.request_trace_id)
        return response

    install_exception_handlers(app)
    app.include_router(create_router())

    return app


app = create_app(storage_settings=StorageSettings(), enforce_auth=True)


def _resolve_obs_trace_status(store: ObservabilityStore | None, trace_id: str) -> Awaitable[str | None]:
    """Phase-four fallback: resolve a trace's status from the OBS pipeline."""

    async def resolver() -> str | None:
        if store is None:
            return None
        canonical_trace_id = (
            trace_id[6:]
            if trace_id.startswith("trace_") and len(trace_id) == 38
            else trace_id
        )
        try:
            trace = await store.get_trace(canonical_trace_id)
        except Exception:  # noqa: BLE001 - resolver must never break reconciliation
            return None
        return trace["status"] if trace else None

    return resolver()


def run() -> None:
    uvicorn.run("tianzhou_agent_platform.main:app", host="0.0.0.0", port=8000, reload=False)


def _is_public_path(path: str) -> bool:
    return path in {"/health", "/openapi.json", "/docs", "/redoc"} or path.startswith("/auth/")
