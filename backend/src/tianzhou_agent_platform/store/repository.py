from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.aina.project import AinaProjectRecord
from tianzhou_agent_platform.aina.memory.models import MemoryRecord
from tianzhou_agent_platform.aina.document.task_models import DocumentEditTask
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.aina.skill.models import SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.conversations.models import Conversation, ConversationCreate, ConversationUpdate, Message
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord
from tianzhou_agent_platform.core.errors import PlatformError, conflict, not_found
from tianzhou_agent_platform.core.feedback import FeedbackRecord
from tianzhou_agent_platform.core.workspace import Workspace, WorkspaceUpdate
from tianzhou_agent_platform.model_providers.models import ModelProviderRecord
from tianzhou_agent_platform.observability.models import LLMCallRecord, TraceRecord
from tianzhou_agent_platform.aina.scheduler import ScheduledAinaExecution, ScheduledAinaTask
from tianzhou_agent_platform.store.memory_repository import (
    AINA_PROJECTS_RESOURCE,
    AINAS_RESOURCE,
    DOCUMENT_EDIT_TASKS_RESOURCE,
    INSTALLATIONS_RESOURCE,
    LLM_CALLS_RESOURCE,
    MEMORIES_RESOURCE,
    MODEL_PROVIDERS_RESOURCE,
    SANDBOXES_RESOURCE,
    SANDBOX_EXECUTIONS_RESOURCE,
    SCHEDULED_AINA_TASKS_RESOURCE,
    SCHEDULED_AINA_EXECUTIONS_RESOURCE,
    SKILLS_RESOURCE,
    TOOLS_RESOURCE,
    TRACES_RESOURCE,
    WORKSPACES_RESOURCE,
    FEEDBACKS_RESOURCE,
    InMemoryRepository,
    INTERRUPTED_RUN_ERROR,
    conversation_with_status,
    finished_run,
    message_from_provider,
    new_conversation,
    started_run,
    updated_conversation,
    with_bound_aina,
)
from tianzhou_agent_platform.store.conversation_store import ConversationStore
from tianzhou_agent_platform.store.user_store import UserStore
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.errors import StorageValidationError
from tianzhou_agent_platform.store.models import StoreQuery
from tianzhou_agent_platform.store.repository_schema import (
    model_from_row,
    repository_metadata as repository_metadata,
    repository_tables as repository_tables,
    row_values,
)
from tianzhou_agent_platform.sandbox.models import SandboxExecution, SandboxRecord

CONVERSATION_RUN_TTL_SECONDS = 15 * 60
CONVERSATION_RUN_HEARTBEAT_SECONDS = 30


class PersistentRepository(InMemoryRepository):
    """MySQL-backed repository with Redis write-through cache and run locks.

    Migration phase four (design 19/17.3): Trace/LLMCall records are no longer
    persisted through the generic repository — they flow through the OTel +
    WAL + OBS MySQL pipeline instead. ``persist_observability=True`` restores
    the legacy behavior for rollback verification windows.
    """

    _OBSERVABILITY_RESOURCES = frozenset({TRACES_RESOURCE, LLM_CALLS_RESOURCE})

    def __init__(
        self,
        stores: StorageStores,
        *,
        persist_observability: bool = False,
        obs_trace_status_resolver: Callable[[str], Awaitable[str | None]] | None = None,
    ) -> None:
        super().__init__()
        self.stores = stores
        self.persist_observability = persist_observability
        self._legacy_observability_load_lock = asyncio.Lock()
        self._legacy_traces_loaded = False
        self._legacy_llm_calls_loaded = False
        # Phase four: conversation run reconciliation falls back to the OBS
        # pipeline when a trace is no longer in the in-memory repository.
        self.obs_trace_status_resolver = obs_trace_status_resolver
        self._conversation_run_tasks: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._conversation_store = ConversationStore(stores.mysql)
        self._user_store = UserStore(stores.mysql)

    async def initialize(self) -> None:
        workspaces = await self._load_models(WORKSPACES_RESOURCE, Workspace)
        memories = await self._load_models(MEMORIES_RESOURCE, MemoryRecord)
        tools = await self._load_models(TOOLS_RESOURCE, ToolRecord)
        skills = await self._load_models(SKILLS_RESOURCE, SkillRecord)
        ainas = await self._load_models(AINAS_RESOURCE, AinaRecord)
        aina_projects = await self._load_models(AINA_PROJECTS_RESOURCE, AinaProjectRecord)
        installations = await self._load_models(INSTALLATIONS_RESOURCE, AinaInstallation)
        model_providers = await self._load_models(MODEL_PROVIDERS_RESOURCE, ModelProviderRecord)
        scheduled_tasks = await self._load_models(SCHEDULED_AINA_TASKS_RESOURCE, ScheduledAinaTask)
        scheduled_executions = await self._load_models(
            SCHEDULED_AINA_EXECUTIONS_RESOURCE,
            ScheduledAinaExecution,
        )
        document_edit_tasks = await self._load_models(DOCUMENT_EDIT_TASKS_RESOURCE, DocumentEditTask)
        sandboxes = await self._load_models(SANDBOXES_RESOURCE, SandboxRecord)
        sandbox_executions = await self._load_models(SANDBOX_EXECUTIONS_RESOURCE, SandboxExecution)
        feedbacks = await self._load_models(FEEDBACKS_RESOURCE, FeedbackRecord)
        traces = (
            await self._load_models(TRACES_RESOURCE, TraceRecord)
            if self.persist_observability
            else []
        )
        llm_calls = (
            await self._load_models(LLM_CALLS_RESOURCE, LLMCallRecord)
            if self.persist_observability
            else []
        )

        async with self._lock:
            self._workspaces = {item.id: item for item in workspaces}
            self._memories = {item.id: item for item in memories}
            self._tools = {item.tool_id: item for item in tools}
            self._skills = {item.skill_id: item for item in skills}
            self._ainas = {item.manifest.aina.id: item for item in ainas}
            self._aina_projects = {item.id: item for item in aina_projects}
            self._installations = {
                (item.tenant_id, item.user_id, item.aina_id): item for item in installations
            }
            # The normal OBS path does not load historical Trace/LLMCall rows
            # at startup. The rollback switch restores the complete legacy
            # behavior, including restart recovery.
            self._traces = {item.trace_id: item for item in traces}
            self._llm_calls = {item.call_id: item for item in llm_calls}
            self._legacy_traces_loaded = self.persist_observability
            self._legacy_llm_calls_loaded = self.persist_observability
            self._model_providers = {item.id: item for item in model_providers}
            self._scheduled_aina_tasks = {item.id: item for item in scheduled_tasks}
            self._scheduled_aina_executions = {
                item.id: item for item in scheduled_executions
            }
            self._document_edit_tasks = {item.id: item for item in document_edit_tasks}
            self._sandboxes = {item.id: item for item in sandboxes}
            self._sandbox_executions = {item.id: item for item in sandbox_executions}
            self._feedbacks = {item.id: item for item in feedbacks}

    async def create_user(self, user: UserRecord) -> UserRecord:
        await self._user_store.create(user)
        return self._copy(user)

    async def find_user_by_id(self, user_id: str) -> UserRecord | None:
        return await self._user_store.get(user_id)

    async def find_user_by_email(self, email: str) -> UserRecord | None:
        return await self._user_store.find_by_email(email)

    async def list_users(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[UserRecord]:
        return await self._user_store.list_users(query=query, limit=limit, offset=offset)

    async def upsert_github_user(
        self,
        *,
        github_id: str,
        github_login: str,
        email: str,
        name: str,
        avatar_url: str | None,
    ) -> UserRecord:
        return await self._user_store.upsert_github(
            github_id=github_id,
            github_login=github_login,
            email=email,
            name=name,
            avatar_url=avatar_url,
        )

    async def get_workspace(self, workspace_id: str) -> Workspace:
        record = await self.stores.mysql.read(WORKSPACES_RESOURCE, workspace_id)
        if record is None:
            async with self._lock:
                self._workspaces.pop(workspace_id, None)
            raise not_found("Workspace", workspace_id)
        workspace = model_from_row(Workspace, record)
        async with self._lock:
            self._workspaces[workspace.id] = workspace
        return self._copy(workspace)

    async def list_workspaces(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[Workspace]:
        await self._refresh_workspaces()
        return await super().list_workspaces(user_id=user_id, tenant_id=tenant_id)

    async def update_workspace(self, workspace_id: str, data: WorkspaceUpdate) -> Workspace:
        async with self.stores.redis.lease(
            "workspace-write",
            workspace_id,
            ttl_seconds=30,
        ) as acquired:
            if not acquired:
                raise conflict("Workspace is being updated. Retry the request.")
            await self.get_workspace(workspace_id)
            return await super().update_workspace(workspace_id, data)

    async def _refresh_workspaces(self) -> list[Workspace]:
        workspaces = await self._load_models(WORKSPACES_RESOURCE, Workspace)
        async with self._lock:
            self._workspaces = {item.id: item for item in workspaces}
        return workspaces

    async def create_aina_project(self, project: AinaProjectRecord) -> AinaProjectRecord:
        async with self.stores.redis.lease(
            "aina-project-write",
            project.id,
            ttl_seconds=30,
        ) as acquired:
            if not acquired:
                existing = await self._load_aina_project(project.id)
                if existing is not None:
                    return self._copy(self._resolve_aina_project_import(existing, project))
                raise conflict("AINA project is being imported. Retry the request.")

            existing = await self._load_aina_project(project.id)
            if existing is not None:
                result = self._resolve_aina_project_import(existing, project)
            else:
                try:
                    await self.stores.mysql.create(
                        AINA_PROJECTS_RESOURCE,
                        row_values(AINA_PROJECTS_RESOURCE, project.id, project),
                    )
                    result = project
                except StorageValidationError:
                    existing = await self._load_aina_project(project.id)
                    if existing is None:
                        raise
                    result = self._resolve_aina_project_import(existing, project)

            async with self._lock:
                self._aina_projects[result.id] = result
            await self.stores.redis.set(
                f"repository:{AINA_PROJECTS_RESOURCE}",
                result.id,
                result.model_dump(mode="json"),
            )
            return self._copy(result)

    async def mark_aina_project_validated(
        self,
        project_id: str,
        *,
        archive_sha256: str,
        user_id: str,
        tenant_id: str,
    ) -> AinaProjectRecord:
        async with self.stores.redis.lease(
            "aina-project-write",
            project_id,
            ttl_seconds=30,
        ) as acquired:
            if not acquired:
                project = await self._load_aina_project(project_id)
                if project is not None:
                    if project.user_id != user_id or project.tenant_id != tenant_id:
                        raise PlatformError(
                            "PERMISSION_DENIED",
                            "AINA project ownership does not match the caller",
                            status_code=403,
                        )
                    if project.archive_sha256 != archive_sha256:
                        raise conflict("AINA project archive changed while it was being imported")
                    if project.status in {"validated", "deployed"}:
                        return self._copy(project)
                raise PlatformError(
                    "CONFLICT",
                    "AINA project is being imported. Retry the request.",
                    status_code=409,
                    retryable=True,
                )
            project = await self._load_aina_project(project_id)
            if project is None:
                raise not_found("AINA project", project_id)
            if project.user_id != user_id or project.tenant_id != tenant_id:
                raise PlatformError(
                    "PERMISSION_DENIED",
                    "AINA project ownership does not match the caller",
                    status_code=403,
                )
            if project.archive_sha256 != archive_sha256:
                raise conflict("AINA project archive changed while it was being imported")
            if project.status in {"validated", "deployed"}:
                return self._copy(project)

            updated = project.model_copy(
                update={
                    "status": "validated",
                    "updated_at": datetime.now(UTC),
                },
                deep=True,
            )
            payload = updated.model_dump(mode="json")
            await self.stores.mysql.update(
                AINA_PROJECTS_RESOURCE,
                project_id,
                row_values(AINA_PROJECTS_RESOURCE, project_id, updated),
            )
            async with self._lock:
                self._aina_projects[project_id] = updated
            await self.stores.redis.set(
                f"repository:{AINA_PROJECTS_RESOURCE}",
                project_id,
                payload,
            )
            return self._copy(updated)

    async def get_aina_project(
        self,
        project_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> AinaProjectRecord:
        project = await self._load_aina_project(project_id)
        async with self._lock:
            if project is None:
                self._aina_projects.pop(project_id, None)
            else:
                self._aina_projects[project.id] = project
        return await super().get_aina_project(project_id, user_id=user_id, tenant_id=tenant_id)

    async def list_aina_projects(self, *, user_id: str, tenant_id: str) -> list[AinaProjectRecord]:
        await self._refresh_aina_projects()
        return await super().list_aina_projects(user_id=user_id, tenant_id=tenant_id)

    async def remove_aina_project(
        self,
        project_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> None:
        async with self.stores.redis.lease(
            "aina-project-write",
            project_id,
            ttl_seconds=30,
        ) as acquired:
            if not acquired:
                raise conflict("AINA project is being updated. Retry the request.")
            await self.get_aina_project(project_id, user_id=user_id, tenant_id=tenant_id)
            deleted = await self.stores.mysql.delete(AINA_PROJECTS_RESOURCE, project_id)
            if not deleted.deleted:
                raise not_found("AINA project", project_id)
            async with self._lock:
                self._aina_projects.pop(project_id, None)
            await self.stores.redis.delete(f"repository:{AINA_PROJECTS_RESOURCE}", project_id)

    async def _refresh_aina_projects(self) -> list[AinaProjectRecord]:
        projects = await self._load_models(AINA_PROJECTS_RESOURCE, AinaProjectRecord)
        async with self._lock:
            self._aina_projects = {project.id: project for project in projects}
        return projects

    async def _load_aina_project(self, project_id: str) -> AinaProjectRecord | None:
        record = await self.stores.mysql.read(AINA_PROJECTS_RESOURCE, project_id)
        if record is None:
            return None
        return model_from_row(AinaProjectRecord, record)

    async def get_sandbox_for_actor(
        self,
        *,
        user_id: str,
        tenant_id: str,
        workspace_id: str | None = None,
    ) -> SandboxRecord:
        sandboxes = await self._load_models(SANDBOXES_RESOURCE, SandboxRecord)
        async with self._lock:
            self._sandboxes = {item.id: item for item in sandboxes}
        return await super().get_sandbox_for_actor(
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )

    async def list_sandbox_executions(
        self,
        *,
        user_id: str,
        tenant_id: str,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[SandboxExecution]:
        executions = await self._load_models(SANDBOX_EXECUTIONS_RESOURCE, SandboxExecution)
        async with self._lock:
            self._sandbox_executions = {item.id: item for item in executions}
        return await super().list_sandbox_executions(
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            limit=limit,
        )

    async def remove_sandbox(self, sandbox_id: str) -> None:
        sandboxes = await self._load_models(SANDBOXES_RESOURCE, SandboxRecord)
        executions = await self._load_models(SANDBOX_EXECUTIONS_RESOURCE, SandboxExecution)
        async with self._lock:
            self._sandboxes = {item.id: item for item in sandboxes}
            self._sandbox_executions = {item.id: item for item in executions}
        await super().remove_sandbox(sandbox_id)

    async def get_document_edit_task(
        self,
        task_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentEditTask:
        record = await self.stores.mysql.read(DOCUMENT_EDIT_TASKS_RESOURCE, task_id)
        if record is not None:
            task = model_from_row(DocumentEditTask, record)
            async with self._lock:
                self._document_edit_tasks[task.id] = task
        return await super().get_document_edit_task(task_id, user_id=user_id, tenant_id=tenant_id)

    async def list_document_edit_tasks(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        document_name: str | None = None,
        workspace_id: str | None = None,
    ) -> list[DocumentEditTask]:
        tasks = await self._load_models(DOCUMENT_EDIT_TASKS_RESOURCE, DocumentEditTask)
        async with self._lock:
            self._document_edit_tasks = {item.id: item for item in tasks}
        return await super().list_document_edit_tasks(
            user_id=user_id,
            tenant_id=tenant_id,
            document_name=document_name,
            workspace_id=workspace_id,
        )

    async def put_document_edit_task(
        self,
        task: DocumentEditTask,
        *,
        expected_version: int,
    ) -> DocumentEditTask:
        acquired = await self.stores.redis.set_if_absent(
            "document-edit-task-write",
            task.id,
            {"expected_version": expected_version},
            ttl_seconds=30,
        )
        if not acquired.written:
            raise conflict("Document edit task is being updated. Reload it before retrying.")
        try:
            record = await self.stores.mysql.read(DOCUMENT_EDIT_TASKS_RESOURCE, task.id)
            if record is not None:
                current = model_from_row(DocumentEditTask, record)
                async with self._lock:
                    self._document_edit_tasks[current.id] = current
            return await super().put_document_edit_task(task, expected_version=expected_version)
        finally:
            await self.stores.redis.delete("document-edit-task-write", task.id)

    async def list_scheduled_aina_tasks(self) -> list[ScheduledAinaTask]:
        # Every node refreshes from MySQL so updates made by the elected node
        # become visible to all schedulers without process-local cache drift.
        tasks = await self._load_models(SCHEDULED_AINA_TASKS_RESOURCE, ScheduledAinaTask)
        async with self._lock:
            self._scheduled_aina_tasks = {item.id: item for item in tasks}
        return [self._copy(item) for item in tasks]

    async def list_scheduled_aina_executions(
        self, task_id: str, *, limit: int = 50
    ) -> list[ScheduledAinaExecution]:
        executions = await self._load_models(
            SCHEDULED_AINA_EXECUTIONS_RESOURCE,
            ScheduledAinaExecution,
        )
        async with self._lock:
            self._scheduled_aina_executions = {item.id: item for item in executions}
        matching = [item for item in executions if item.task_id == task_id]
        return sorted(matching, key=lambda item: item.started_at, reverse=True)[:limit]

    async def start_conversation_run(self, conversation_id: str, trace_id: str) -> Conversation:
        async with self.stores.redis.lease(
            "conversation-run-state", conversation_id, ttl_seconds=30, blocking_timeout_seconds=5,
        ) as locked:
            if not locked:
                raise conflict("Conversation run state is being updated. Retry the request.")
            conversation = await self.get_conversation(conversation_id)
            acquired = await self.stores.redis.set_if_absent(
                "conversation-run", conversation_id, {"trace_id": trace_id},
                ttl_seconds=CONVERSATION_RUN_TTL_SECONDS,
            )
            if not acquired.written:
                raise conflict("This conversation already has a running request")
            try:
                # Owning the lease proves the previous worker no longer owns this run.
                if conversation.run_status == "running":
                    await self._finish_run_row(conversation_id, status="failed", error=INTERRUPTED_RUN_ERROR)
                conversation = await self._conversation_store.mutate(
                    conversation_id, lambda current: started_run(current, trace_id)
                )
            except BaseException:
                await self.stores.redis.delete_if_value("conversation-run", conversation_id, {"trace_id": trace_id})
                raise
            heartbeat = asyncio.create_task(
                self._renew_conversation_run(conversation_id, trace_id, asyncio.current_task())
            )
            self._conversation_run_tasks[(conversation_id, trace_id)] = heartbeat
            return conversation

    async def _renew_conversation_run(
        self, conversation_id: str, trace_id: str, owner: asyncio.Task[Any] | None,
    ) -> None:
        try:
            while owner is not None and not owner.done():
                await asyncio.sleep(CONVERSATION_RUN_HEARTBEAT_SECONDS)
                try:
                    renewed = await self.stores.redis.refresh_if_value(
                        "conversation-run", conversation_id, {"trace_id": trace_id},
                        ttl_seconds=CONVERSATION_RUN_TTL_SECONDS,
                    )
                    if renewed.written:
                        continue
                except Exception:
                    pass
                # Stop execution when ownership cannot be established; do not replay effects.
                owner.cancel()
                return
        finally:
            if self._conversation_run_tasks.get((conversation_id, trace_id)) is asyncio.current_task():
                self._conversation_run_tasks.pop((conversation_id, trace_id))

    async def reconcile_conversation_run(self, conversation_id: str) -> Conversation:
        async with self.stores.redis.lease("conversation-run-state", conversation_id, ttl_seconds=30) as locked:
            if not locked:
                return await self.get_conversation(conversation_id)
            conversation = await self.get_conversation(conversation_id)
            if conversation.run_status == "running":
                entry = await self.stores.redis.get("conversation-run", conversation_id)
                if entry is None:
                    return await self._finish_run_row(conversation_id, status="failed", error=INTERRUPTED_RUN_ERROR)
        return await super().reconcile_conversation_run(conversation_id)

    async def _has_live_conversation_run(self, conversation: Conversation) -> bool:
        entry = await self.stores.redis.get("conversation-run", conversation.id)
        return entry is not None and entry.value == {"trace_id": conversation.active_trace_id}

    async def finish_conversation_run(
        self,
        conversation_id: str,
        *,
        status: str = "idle",
        error: str | None = None,
        expected_trace_id: str | None = None,
    ) -> Conversation:
        local_traces = [key[1] for key in self._conversation_run_tasks if key[0] == conversation_id]
        trace_id = expected_trace_id or (local_traces[0] if len(local_traces) == 1 else None)
        heartbeat = self._conversation_run_tasks.get((conversation_id, trace_id)) if trace_id else None
        try:
            async with self.stores.redis.lease(
                "conversation-run-state", conversation_id, ttl_seconds=30, blocking_timeout_seconds=5,
            ) as locked:
                if not locked:
                    raise conflict("Conversation run state is being updated. Retry the request.")
                current = await self.get_conversation(conversation_id)
                if trace_id is not None and current.active_trace_id != trace_id:
                    return current
                trace_id = trace_id or current.active_trace_id
                conversation = await self._finish_run_row(
                    conversation_id, status=status, error=error, expected_trace_id=trace_id,
                )
                if trace_id is not None:
                    await self.stores.redis.delete_if_value("conversation-run", conversation_id, {"trace_id": trace_id})
                return conversation
        finally:
            if heartbeat is not None:
                self._conversation_run_tasks.pop((conversation_id, trace_id), None)
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    async def _finish_run_row(
        self,
        conversation_id: str,
        *,
        status: str,
        error: str | None,
        expected_trace_id: str | None = None,
    ) -> Conversation:
        def change(conversation: Conversation) -> Conversation | None:
            if expected_trace_id is not None and conversation.active_trace_id != expected_trace_id:
                return None
            return finished_run(conversation, status=status, error=error)

        return await self._conversation_store.mutate(conversation_id, change, include_deleted=True)

    async def create_conversation(self, data: ConversationCreate) -> Conversation:
        if data.workspace_id is not None:
            await self.require_workspace_actor(data.workspace_id, user_id=data.user_id, tenant_id=data.tenant_id)
        conversation = new_conversation(data)
        await self._conversation_store.create(conversation)
        return conversation

    async def get_conversation(self, conversation_id: str, *, include_deleted: bool = False) -> Conversation:
        conversation = await self._conversation_store.get(conversation_id, include_deleted=include_deleted)
        if conversation is None:
            raise not_found("Conversation", conversation_id)
        return conversation

    async def list_conversations(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        category: str | None = None,
        workspace_id: str | None = None,
    ) -> list[Conversation]:
        return await self._conversation_store.list_conversations(
            user_id=user_id, tenant_id=tenant_id, category=category, workspace_id=workspace_id
        )

    async def update_conversation(self, conversation_id: str, data: ConversationUpdate) -> Conversation:
        return await self._conversation_store.mutate(
            conversation_id, lambda conversation: updated_conversation(conversation, data)
        )

    async def set_conversation_status(self, conversation_id: str, status: str) -> Conversation:
        return await self._conversation_store.mutate(
            conversation_id, lambda conversation: conversation_with_status(conversation, status), include_deleted=True
        )

    async def bind_conversation_aina(
        self,
        conversation_id: str,
        aina_id: str,
        *,
        make_primary: bool = False,
        mark_used: bool = False,
    ) -> Conversation:
        return await self._conversation_store.mutate(
            conversation_id,
            lambda conversation: with_bound_aina(
                conversation, aina_id, make_primary=make_primary, mark_used=mark_used
            ),
        )

    async def append_provider_messages(
        self,
        conversation_id: str,
        messages: Iterable[dict[str, Any]],
        *,
        trace_id: str,
    ) -> list[Message]:
        appended = await self._conversation_store.append(
            conversation_id,
            [message_from_provider(raw, trace_id=trace_id) for raw in messages],
            require_active=True,
        )
        if appended is None:
            raise not_found("Conversation", conversation_id)
        return appended

    async def append_messages(self, conversation_id: str, messages: Iterable[Message]) -> Conversation | None:
        if await self._conversation_store.append(conversation_id, messages, require_active=False) is None:
            return None
        return await self.get_conversation(conversation_id, include_deleted=True)

    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        await self._conversation_store.create_approval(approval)
        return self._copy(approval)

    async def get_approval(self, approval_id: str) -> ApprovalRecord:
        approval = await self._conversation_store.get_approval(approval_id)
        if approval is None:
            raise not_found("Approval", approval_id)
        return approval

    async def list_approvals(
        self,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        status: str | None = None,
    ) -> list[ApprovalRecord]:
        return await self._conversation_store.list_approvals(
            conversation_id=conversation_id, user_id=user_id, tenant_id=tenant_id, status=status
        )

    async def set_approval_status(self, approval_id: str, status: str) -> ApprovalRecord:
        approval = await self._conversation_store.set_approval_status(approval_id, status)
        if approval is None:
            raise not_found("Approval", approval_id)
        return approval

    async def cancel_pending_approvals(self, conversation_id: str) -> list[ApprovalRecord]:
        denied = await self._conversation_store.deny_pending_approvals(conversation_id)
        # As in InMemoryRepository: only approvals whose trace this process holds are reported for closing.
        return [approval for approval in denied if approval.trace_id in self._traces]

    async def get_trace(self, trace_id: str) -> TraceRecord:
        try:
            return await super().get_trace(trace_id)
        except PlatformError as exc:
            if exc.code != "RESOURCE_NOT_FOUND" or self._legacy_traces_loaded:
                raise
            # Migration fallback: look up one legacy row on demand without
            # bringing the complete historical trace table into memory.
            record = await self.stores.mysql.read(TRACES_RESOURCE, trace_id)
            if record is None:
                raise
            trace = model_from_row(TraceRecord, record)
            async with self._lock:
                self._traces.setdefault(trace.trace_id, trace)
            return self._copy(trace)

    async def list_traces(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[TraceRecord]:
        await self._load_legacy_traces_on_demand()
        return await super().list_traces(user_id=user_id, tenant_id=tenant_id)

    async def list_llm_calls(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        trace_ids: set[str] | None = None,
        context_ids: set[str] | None = None,
    ) -> list[LLMCallRecord]:
        await self._load_legacy_llm_calls_on_demand()
        return await super().list_llm_calls(
            limit=limit,
            offset=offset,
            trace_ids=trace_ids,
            context_ids=context_ids,
        )

    async def count_llm_calls(
        self,
        *,
        trace_ids: set[str] | None = None,
        context_ids: set[str] | None = None,
    ) -> int:
        await self._load_legacy_llm_calls_on_demand()
        return await super().count_llm_calls(
            trace_ids=trace_ids,
            context_ids=context_ids,
        )

    async def _load_legacy_traces_on_demand(self) -> None:
        if self._legacy_traces_loaded:
            return
        async with self._legacy_observability_load_lock:
            if self._legacy_traces_loaded:
                return
            traces = await self._load_models(
                TRACES_RESOURCE,
                TraceRecord,
                cache_in_redis=False,
            )
            async with self._lock:
                for trace in traces:
                    self._traces.setdefault(trace.trace_id, trace)
            self._legacy_traces_loaded = True

    async def _load_legacy_llm_calls_on_demand(self) -> None:
        if self._legacy_llm_calls_loaded:
            return
        async with self._legacy_observability_load_lock:
            if self._legacy_llm_calls_loaded:
                return
            calls = await self._load_models(
                LLM_CALLS_RESOURCE,
                LLMCallRecord,
                cache_in_redis=False,
            )
            async with self._lock:
                for call in calls:
                    self._llm_calls.setdefault(call.call_id, call)
            self._legacy_llm_calls_loaded = True

    async def _save_record(self, resource: str, record_id: str, value: Any) -> None:
        if not isinstance(value, BaseModel):
            raise TypeError(f"Persistent repository value for {resource!r} is not a Pydantic model")
        # Phase four: Trace/LLMCall stay in memory only; the WAL + OBS MySQL
        # pipeline owns their durability now.
        if resource in self._OBSERVABILITY_RESOURCES and not self.persist_observability:
            return
        values = row_values(resource, record_id, value)
        existing = await self.stores.mysql.read(resource, record_id)
        if existing is None:
            await self.stores.mysql.create(resource, values)
        else:
            await self.stores.mysql.update(resource, record_id, values)
        await self.stores.redis.set(f"repository:{resource}", record_id, value.model_dump(mode="json"))

    async def _delete_record(self, resource: str, record_id: str) -> None:
        if resource in self._OBSERVABILITY_RESOURCES and not self.persist_observability:
            return
        await self.stores.mysql.delete(resource, record_id)
        await self.stores.redis.delete(f"repository:{resource}", record_id)

    async def _load_models[ModelT: BaseModel](
        self,
        resource: str,
        model: type[ModelT],
        *,
        cache_in_redis: bool = True,
    ) -> list[ModelT]:
        values: list[ModelT] = []
        offset = 0
        while True:
            page = await self.stores.mysql.query(resource, StoreQuery(limit=1000, offset=offset))
            for record in page.items:
                item = model_from_row(model, record)
                values.append(item)
                if cache_in_redis:
                    await self.stores.redis.set(
                        f"repository:{resource}", str(record.id), item.model_dump(mode="json")
                    )
            if len(page.items) < page.limit:
                break
            offset += len(page.items)
        return values
