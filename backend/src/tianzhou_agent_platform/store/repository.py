from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import JSON, Column, DateTime, MetaData, String, Table

from tianzhou_agent_platform.aina.memory.models import MemoryRecord
from tianzhou_agent_platform.aina.document.task_models import DocumentEditTask
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.aina.skill.models import SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.core.chat import ApprovalRecord, TraceRecord
from tianzhou_agent_platform.core.conversation import Conversation
from tianzhou_agent_platform.core.errors import conflict
from tianzhou_agent_platform.core.model_settings import ModelProviderRecord
from tianzhou_agent_platform.aina.scheduler import ScheduledAinaExecution, ScheduledAinaTask
from tianzhou_agent_platform.core.repository import (
    AINAS_RESOURCE,
    APPROVALS_RESOURCE,
    CONVERSATIONS_RESOURCE,
    DOCUMENT_EDIT_TASKS_RESOURCE,
    INSTALLATIONS_RESOURCE,
    MEMORIES_RESOURCE,
    MODEL_PROVIDERS_RESOURCE,
    SANDBOXES_RESOURCE,
    SANDBOX_EXECUTIONS_RESOURCE,
    SCHEDULED_AINA_TASKS_RESOURCE,
    SCHEDULED_AINA_EXECUTIONS_RESOURCE,
    SKILLS_RESOURCE,
    TOOLS_RESOURCE,
    TRACES_RESOURCE,
    InMemoryRepository,
)
from tianzhou_agent_platform.store.lifecycle import StorageStores
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
        INSTALLATIONS_RESOURCE,
        TRACES_RESOURCE,
        APPROVALS_RESOURCE,
        MODEL_PROVIDERS_RESOURCE,
        SCHEDULED_AINA_TASKS_RESOURCE,
        SCHEDULED_AINA_EXECUTIONS_RESOURCE,
        DOCUMENT_EDIT_TASKS_RESOURCE,
        SANDBOXES_RESOURCE,
        SANDBOX_EXECUTIONS_RESOURCE,
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
        installations = await self._load_models(INSTALLATIONS_RESOURCE, AinaInstallation)
        traces = await self._load_models(TRACES_RESOURCE, TraceRecord)
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

        async with self._lock:
            self._conversations = {item.id: item for item in conversations}
            self._memories = {item.id: item for item in memories}
            self._tools = {item.tool_id: item for item in tools}
            self._skills = {item.skill_id: item for item in skills}
            self._ainas = {item.manifest.aina.id: item for item in ainas}
            self._installations = {
                (item.tenant_id, item.user_id, item.aina_id): item for item in installations
            }
            self._traces = {item.trace_id: item for item in traces}
            self._approvals = {item.id: item for item in approvals}
            self._model_providers = {item.id: item for item in model_providers}
            self._scheduled_aina_tasks = {item.id: item for item in scheduled_tasks}
            self._scheduled_aina_executions = {
                item.id: item for item in scheduled_executions
            }
            self._document_edit_tasks = {item.id: item for item in document_edit_tasks}
            self._sandboxes = {item.id: item for item in sandboxes}
            self._sandbox_executions = {item.id: item for item in sandbox_executions}

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
    ) -> list[DocumentEditTask]:
        tasks = await self._load_models(DOCUMENT_EDIT_TASKS_RESOURCE, DocumentEditTask)
        async with self._lock:
            self._document_edit_tasks = {item.id: item for item in tasks}
        return await super().list_document_edit_tasks(
            user_id=user_id,
            tenant_id=tenant_id,
            document_name=document_name,
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
