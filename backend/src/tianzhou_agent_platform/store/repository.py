from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table

from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.aina.project import AinaProjectRecord
from tianzhou_agent_platform.aina.memory.models import MemoryRecord
from tianzhou_agent_platform.aina.document.task_models import DocumentEditTask
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.aina.protocol.schedule import ScheduledAinaExecution, ScheduledAinaTask
from tianzhou_agent_platform.aina.skill.models import SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.core.chat import ApprovalRecord, LLMCallRecord, TraceRecord
from tianzhou_agent_platform.core.feedback import FeedbackRecord
from tianzhou_agent_platform.core.conversation import Conversation
from tianzhou_agent_platform.core.errors import PlatformError, conflict, not_found
from tianzhou_agent_platform.core.model_settings import ModelProviderRecord
from tianzhou_agent_platform.core.repository import (
    AINA_PROJECTS_RESOURCE,
    AINAS_RESOURCE,
    APPROVALS_RESOURCE,
    CONVERSATIONS_RESOURCE,
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
    USERS_RESOURCE,
    FEEDBACKS_RESOURCE,
    InMemoryRepository,
)
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.errors import StorageValidationError
from tianzhou_agent_platform.store.models import StoreQuery
from tianzhou_agent_platform.sandbox.models import SandboxExecution, SandboxRecord

repository_metadata = MetaData()


def _record_table(name: str) -> Table:
    return Table(
        f"unibot_{name}",
        repository_metadata,
        Column("id", String(255), primary_key=True),
        Column("payload", JSON, nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
    )


repository_tables = {
    resource: _record_table(resource)
    for resource in (
        CONVERSATIONS_RESOURCE,
        MEMORIES_RESOURCE,
        TOOLS_RESOURCE,
        SKILLS_RESOURCE,
        AINAS_RESOURCE,
        AINA_PROJECTS_RESOURCE,
        INSTALLATIONS_RESOURCE,
        LLM_CALLS_RESOURCE,
        TRACES_RESOURCE,
        APPROVALS_RESOURCE,
        MODEL_PROVIDERS_RESOURCE,
        SCHEDULED_AINA_TASKS_RESOURCE,
        SCHEDULED_AINA_EXECUTIONS_RESOURCE,
        DOCUMENT_EDIT_TASKS_RESOURCE,
        SANDBOXES_RESOURCE,
        SANDBOX_EXECUTIONS_RESOURCE,
        USERS_RESOURCE,
        FEEDBACKS_RESOURCE,
    )
}


class PersistentRepository(InMemoryRepository):
    """MySQL-backed repository with Redis write-through cache and run locks."""

    def __init__(self, stores: StorageStores) -> None:
        super().__init__()
        self.stores = stores

    async def initialize(self) -> None:
        await self.stores.mysql.create_tables(repository_metadata)
        conversations = await self._load_models(CONVERSATIONS_RESOURCE, Conversation)
        memories = await self._load_models(MEMORIES_RESOURCE, MemoryRecord)
        tools = await self._load_models(TOOLS_RESOURCE, ToolRecord)
        skills = await self._load_models(SKILLS_RESOURCE, SkillRecord)
        ainas = await self._load_models(AINAS_RESOURCE, AinaRecord)
        aina_projects = await self._load_models(AINA_PROJECTS_RESOURCE, AinaProjectRecord)
        installations = await self._load_models(INSTALLATIONS_RESOURCE, AinaInstallation)
        traces = await self._load_models(TRACES_RESOURCE, TraceRecord)
        llm_calls = await self._load_models(LLM_CALLS_RESOURCE, LLMCallRecord)
        approvals = await self._load_models(APPROVALS_RESOURCE, ApprovalRecord)
        model_providers = await self._load_models(MODEL_PROVIDERS_RESOURCE, ModelProviderRecord)
        scheduled_tasks = await self._load_models(SCHEDULED_AINA_TASKS_RESOURCE, ScheduledAinaTask)
        scheduled_executions = await self._load_models(
            SCHEDULED_AINA_EXECUTIONS_RESOURCE,
            ScheduledAinaExecution,
        )
        document_edit_tasks = await self._load_models(DOCUMENT_EDIT_TASKS_RESOURCE, DocumentEditTask)
        sandboxes = await self._load_models(SANDBOXES_RESOURCE, SandboxRecord)
        sandbox_executions = await self._load_models(SANDBOX_EXECUTIONS_RESOURCE, SandboxExecution)
        users = await self._load_models(USERS_RESOURCE, UserRecord)
        feedbacks = await self._load_models(FEEDBACKS_RESOURCE, FeedbackRecord)

        async with self._lock:
            self._conversations = {item.id: item for item in conversations}
            self._memories = {item.id: item for item in memories}
            self._tools = {item.tool_id: item for item in tools}
            self._skills = {item.skill_id: item for item in skills}
            self._ainas = {item.manifest.aina.id: item for item in ainas}
            self._aina_projects = {item.id: item for item in aina_projects}
            self._installations = {
                (item.tenant_id, item.user_id, item.aina_id): item for item in installations
            }
            self._traces = {item.trace_id: item for item in traces}
            self._llm_calls = {item.call_id: item for item in llm_calls}
            self._approvals = {item.id: item for item in approvals}
            self._model_providers = {item.id: item for item in model_providers}
            self._scheduled_aina_tasks = {item.id: item for item in scheduled_tasks}
            self._scheduled_aina_executions = {
                item.id: item for item in scheduled_executions
            }
            self._document_edit_tasks = {item.id: item for item in document_edit_tasks}
            self._sandboxes = {item.id: item for item in sandboxes}
            self._sandbox_executions = {item.id: item for item in sandbox_executions}
            self._users = {item.id: item for item in users}
            self._feedbacks = {item.id: item for item in feedbacks}

    async def create_user(self, user: UserRecord) -> UserRecord:
        async with self.stores.redis.lease("auth-user-write", "users", ttl_seconds=30) as acquired:
            if not acquired:
                raise conflict("User registration is already in progress")
            await self._refresh_users()
            return await super().create_user(user)

    async def find_user_by_id(self, user_id: str) -> UserRecord | None:
        record = await self.stores.mysql.read(USERS_RESOURCE, user_id)
        if record is None:
            async with self._lock:
                self._users.pop(user_id, None)
            return None
        user = UserRecord.model_validate(record.values["payload"])
        async with self._lock:
            self._users[user.id] = user
        return self._copy(user)

    async def find_user_by_email(self, email: str) -> UserRecord | None:
        await self._refresh_users()
        return await super().find_user_by_email(email)

    async def upsert_github_user(
        self,
        *,
        github_id: str,
        github_login: str,
        email: str,
        name: str,
        avatar_url: str | None,
    ) -> UserRecord:
        async with self.stores.redis.lease("auth-user-write", "users", ttl_seconds=30) as acquired:
            if not acquired:
                raise conflict("GitHub sign-in is already in progress")
            await self._refresh_users()
            return await super().upsert_github_user(
                github_id=github_id,
                github_login=github_login,
                email=email,
                name=name,
                avatar_url=avatar_url,
            )

    async def _refresh_users(self) -> list[UserRecord]:
        users = await self._load_models(USERS_RESOURCE, UserRecord)
        async with self._lock:
            self._users = {item.id: item for item in users}
        return users

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
                payload = project.model_dump(mode="json")
                values = {"id": project.id, "payload": payload, "updated_at": datetime.now(UTC)}
                try:
                    await self.stores.mysql.create(AINA_PROJECTS_RESOURCE, values)
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
                {"payload": payload, "updated_at": datetime.now(UTC)},
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
        return AinaProjectRecord.model_validate(record.values["payload"])

    async def get_sandbox_for_actor(self, *, user_id: str, tenant_id: str) -> SandboxRecord:
        sandboxes = await self._load_models(SANDBOXES_RESOURCE, SandboxRecord)
        async with self._lock:
            self._sandboxes = {item.id: item for item in sandboxes}
        return await super().get_sandbox_for_actor(user_id=user_id, tenant_id=tenant_id)

    async def list_sandbox_executions(
        self,
        *,
        user_id: str,
        tenant_id: str,
        limit: int = 50,
    ) -> list[SandboxExecution]:
        executions = await self._load_models(SANDBOX_EXECUTIONS_RESOURCE, SandboxExecution)
        async with self._lock:
            self._sandbox_executions = {item.id: item for item in executions}
        return await super().list_sandbox_executions(
            user_id=user_id,
            tenant_id=tenant_id,
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
            task = DocumentEditTask.model_validate(record.values["payload"])
            async with self._lock:
                self._document_edit_tasks[task.id] = task
        return await super().get_document_edit_task(task_id, user_id=user_id, tenant_id=tenant_id)

    async def list_document_edit_tasks(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        document_name: str | None = None,
        statuses: set[str] | None = None,
    ) -> list[DocumentEditTask]:
        tasks = await self._load_models(DOCUMENT_EDIT_TASKS_RESOURCE, DocumentEditTask)
        async with self._lock:
            self._document_edit_tasks = {item.id: item for item in tasks}
        return await super().list_document_edit_tasks(
            user_id=user_id,
            tenant_id=tenant_id,
            document_name=document_name,
            statuses=statuses,
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
                current = DocumentEditTask.model_validate(record.values["payload"])
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
        acquired = await self.stores.redis.set_if_absent(
            "conversation-run",
            conversation_id,
            {"trace_id": trace_id},
            ttl_seconds=15 * 60,
        )
        if not acquired.written:
            raise conflict("This conversation already has a running request")
        try:
            return await super().start_conversation_run(conversation_id, trace_id)
        except Exception:
            await self.stores.redis.delete("conversation-run", conversation_id)
            raise

    async def finish_conversation_run(
        self,
        conversation_id: str,
        *,
        status: str = "idle",
        error: str | None = None,
    ) -> Conversation:
        conversation = await super().finish_conversation_run(conversation_id, status=status, error=error)
        await self.stores.redis.delete("conversation-run", conversation_id)
        return conversation

    async def _save_record(self, resource: str, record_id: str, value: Any) -> None:
        if not isinstance(value, BaseModel):
            raise TypeError(f"Persistent repository value for {resource!r} is not a Pydantic model")
        payload = value.model_dump(mode="json")
        values = {"payload": payload, "updated_at": datetime.now(UTC)}
        existing = await self.stores.mysql.read(resource, record_id)
        if existing is None:
            await self.stores.mysql.create(resource, {"id": record_id, **values})
        else:
            await self.stores.mysql.update(resource, record_id, values)
        await self.stores.redis.set(f"repository:{resource}", record_id, payload)

    async def _delete_record(self, resource: str, record_id: str) -> None:
        await self.stores.mysql.delete(resource, record_id)
        await self.stores.redis.delete(f"repository:{resource}", record_id)

    async def _load_models[ModelT: BaseModel](
        self,
        resource: str,
        model: type[ModelT],
    ) -> list[ModelT]:
        values: list[ModelT] = []
        offset = 0
        while True:
            page = await self.stores.mysql.query(resource, StoreQuery(limit=1000, offset=offset))
            for record in page.items:
                payload = record.values["payload"]
                item = model.model_validate(payload)
                values.append(item)
                await self.stores.redis.set(f"repository:{resource}", str(record.id), payload)
            if len(page.items) < page.limit:
                break
            offset += len(page.items)
        return values
