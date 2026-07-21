from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any, Iterable
from uuid import uuid4

from tianzhou_agent_platform.aina.memory.models import MemoryCreate, MemoryRecord, MemoryStats, MemoryUpdate
from tianzhou_agent_platform.aina.document.task_models import DocumentEditTask
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.aina.skill.models import SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.aina.scheduler import (
    ScheduledAinaExecution,
    ScheduledAinaTask,
    ScheduledAinaTaskCreate,
    ScheduledAinaTaskUpdate,
    next_scheduled_run,
)
from tianzhou_agent_platform.core.chat import ApprovalRecord, TraceEvent, TraceRecord
from tianzhou_agent_platform.core.conversation import Conversation, ConversationCreate, ConversationUpdate, Message
from tianzhou_agent_platform.core.errors import PlatformError, conflict, not_found
from tianzhou_agent_platform.core.model_settings import (
    ModelDefinition,
    ModelProviderCreate,
    ModelProviderRecord,
    ModelProviderUpdate,
    ModelRuntimeConfig,
)

CONVERSATIONS_RESOURCE = "conversations"
MEMORIES_RESOURCE = "memories"
TOOLS_RESOURCE = "tools"
SKILLS_RESOURCE = "skills"
AINAS_RESOURCE = "ainas"
INSTALLATIONS_RESOURCE = "installations"
TRACES_RESOURCE = "traces"
APPROVALS_RESOURCE = "approvals"
MODEL_PROVIDERS_RESOURCE = "model_providers"
SCHEDULED_AINA_TASKS_RESOURCE = "scheduled_aina_tasks"
INTERRUPTED_RUN_ERROR = "上一次处理未正常结束，请重新发送请求。"
SCHEDULED_AINA_EXECUTIONS_RESOURCE = "scheduled_aina_executions"
DOCUMENT_EDIT_TASKS_RESOURCE = "document_edit_tasks"


class InMemoryRepository:
    """Concurrency-safe MVP repository.

    All runtime code depends on this small method surface rather than on the
    dictionaries directly, so a durable SQL-backed implementation can replace
    it without changing the agent or API contracts.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._conversations: dict[str, Conversation] = {}
        self._tools: dict[str, ToolRecord] = {}
        self._skills: dict[str, SkillRecord] = {}
        self._ainas: dict[str, AinaRecord] = {}
        self._installations: dict[tuple[str, str, str], AinaInstallation] = {}
        self._traces: dict[str, TraceRecord] = {}
        self._approvals: dict[str, ApprovalRecord] = {}
        self._memories: dict[str, MemoryRecord] = {}
        self._model_providers: dict[str, ModelProviderRecord] = {}
        self._scheduled_aina_tasks: dict[str, ScheduledAinaTask] = {}
        self._scheduled_aina_executions: dict[str, ScheduledAinaExecution] = {}
        self._document_edit_tasks: dict[str, DocumentEditTask] = {}

    async def _save_record(self, resource: str, record_id: str, value: Any) -> None:
        return None

    async def _delete_record(self, resource: str, record_id: str) -> None:
        return None

    @staticmethod
    def _copy[T](value: T) -> T:
        if hasattr(value, "model_copy"):
            return value.model_copy(deep=True)  # type: ignore[no-any-return, union-attr]
        return value

    async def create_model_provider(self, data: ModelProviderCreate) -> ModelProviderRecord:
        async with self._lock:
            actor_count = sum(
                item.user_id == data.user_id and item.tenant_id == data.tenant_id
                for item in self._model_providers.values()
            )
            if actor_count >= 20:
                raise conflict("Model provider limit reached")
            provider = ModelProviderRecord(
                id=f"provider_{uuid4().hex}",
                user_id=data.user_id,
                tenant_id=data.tenant_id,
                provider_type=data.provider_type,
                name=data.name,
                base_url=data.base_url,
                api_key=data.api_key or "",
                timeout_seconds=data.timeout_seconds,
                models=[
                    ModelDefinition(
                        id=f"model_{uuid4().hex}",
                        name=model.name,
                        model=model.model,
                        enabled=model.enabled,
                    )
                    for model in data.models
                ],
            )
            self._model_providers[provider.id] = provider
            await self._save_record(MODEL_PROVIDERS_RESOURCE, provider.id, provider)
            return self._copy(provider)

    async def get_model_provider(
        self,
        provider_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> ModelProviderRecord:
        async with self._lock:
            provider = self._model_providers.get(provider_id)
            if provider is None:
                raise not_found("Model provider", provider_id)
            if provider.user_id != user_id or provider.tenant_id != tenant_id:
                raise PlatformError(
                    "PERMISSION_DENIED",
                    "Model provider ownership does not match the caller",
                    status_code=403,
                )
            return self._copy(provider)

    async def list_model_providers(self, *, user_id: str, tenant_id: str) -> list[ModelProviderRecord]:
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._model_providers.values()
                if item.user_id == user_id and item.tenant_id == tenant_id
            ]
        return sorted(values, key=lambda item: item.created_at)

    async def update_model_provider(
        self,
        provider_id: str,
        data: ModelProviderUpdate,
    ) -> ModelProviderRecord:
        async with self._lock:
            provider = self._model_providers.get(provider_id)
            if provider is None:
                raise not_found("Model provider", provider_id)
            if provider.user_id != data.user_id or provider.tenant_id != data.tenant_id:
                raise PlatformError(
                    "PERMISSION_DENIED",
                    "Model provider ownership does not match the caller",
                    status_code=403,
                )
            current_models = {item.id: item for item in provider.models}
            submitted_ids = [item.id for item in data.models if item.id is not None]
            if len(submitted_ids) != len(set(submitted_ids)):
                raise conflict("Model identifiers must be unique")
            unknown_ids = set(submitted_ids) - set(current_models)
            if unknown_ids:
                raise conflict("A submitted model does not belong to this provider")
            models = [
                ModelDefinition(
                    id=model.id or f"model_{uuid4().hex}",
                    name=model.name,
                    model=model.model,
                    enabled=model.enabled,
                    is_default=(current_models[model.id].is_default and model.enabled if model.id else False),
                )
                for model in data.models
            ]
            updated = provider.model_copy(
                update={
                    "provider_type": data.provider_type,
                    "name": data.name,
                    "base_url": data.base_url,
                    "api_key": provider.api_key if data.api_key is None or data.api_key == "" else data.api_key,
                    "timeout_seconds": data.timeout_seconds,
                    "models": models,
                    "updated_at": datetime.now(UTC),
                },
                deep=True,
            )
            self._model_providers[provider_id] = updated
            await self._save_record(MODEL_PROVIDERS_RESOURCE, provider_id, updated)
            return self._copy(updated)

    async def remove_model_provider(self, provider_id: str, *, user_id: str, tenant_id: str) -> None:
        async with self._lock:
            provider = self._model_providers.get(provider_id)
            if provider is None:
                raise not_found("Model provider", provider_id)
            if provider.user_id != user_id or provider.tenant_id != tenant_id:
                raise PlatformError(
                    "PERMISSION_DENIED",
                    "Model provider ownership does not match the caller",
                    status_code=403,
                )
            del self._model_providers[provider_id]
            await self._delete_record(MODEL_PROVIDERS_RESOURCE, provider_id)

    async def set_default_model(
        self,
        provider_id: str,
        model_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> ModelProviderRecord:
        async with self._lock:
            provider = self._model_providers.get(provider_id)
            if provider is None:
                raise not_found("Model provider", provider_id)
            if provider.user_id != user_id or provider.tenant_id != tenant_id:
                raise PlatformError(
                    "PERMISSION_DENIED",
                    "Model provider ownership does not match the caller",
                    status_code=403,
                )
            selected = next((item for item in provider.models if item.id == model_id), None)
            if selected is None:
                raise not_found("Model", model_id)
            if not selected.enabled:
                raise conflict("A disabled model cannot be selected as default")

            changed: list[ModelProviderRecord] = []
            now = datetime.now(UTC)
            for item in list(self._model_providers.values()):
                if item.user_id != user_id or item.tenant_id != tenant_id:
                    continue
                models = [
                    model.model_copy(
                        update={"is_default": item.id == provider_id and model.id == model_id},
                    )
                    for model in item.models
                ]
                if models != item.models:
                    updated = item.model_copy(update={"models": models, "updated_at": now}, deep=True)
                    self._model_providers[item.id] = updated
                    changed.append(updated)
            for item in changed:
                await self._save_record(MODEL_PROVIDERS_RESOURCE, item.id, item)
            return self._copy(self._model_providers[provider_id])

    async def get_default_model_runtime(
        self,
        *,
        user_id: str,
        tenant_id: str,
    ) -> ModelRuntimeConfig | None:
        async with self._lock:
            for provider in self._model_providers.values():
                if provider.user_id != user_id or provider.tenant_id != tenant_id:
                    continue
                model = next((item for item in provider.models if item.is_default and item.enabled), None)
                if model is not None:
                    return ModelRuntimeConfig(
                        provider_id=provider.id,
                        provider_name=provider.name,
                        base_url=provider.base_url,
                        api_key=provider.api_key,
                        model_id=model.id,
                        model_name=model.name,
                        model=model.model,
                        timeout_seconds=provider.timeout_seconds,
                    )
        return None

    async def create_scheduled_aina_task(self, data: ScheduledAinaTaskCreate) -> ScheduledAinaTask:
        now = datetime.now(UTC)
        values = data.model_dump()
        if data.prompt is not None:
            values["input"] = {"message": data.prompt}
        task = ScheduledAinaTask(**values, created_at=now, updated_at=now)
        task = task.model_copy(update={"next_run_at": next_scheduled_run(task, after=now)})
        async with self._lock:
            self._scheduled_aina_tasks[task.id] = task
            await self._save_record(SCHEDULED_AINA_TASKS_RESOURCE, task.id, task)
        return self._copy(task)

    async def create_document_edit_task(self, task: DocumentEditTask) -> DocumentEditTask:
        async with self._lock:
            self._document_edit_tasks[task.id] = task
            await self._save_record(DOCUMENT_EDIT_TASKS_RESOURCE, task.id, task)
        return self._copy(task)

    async def get_document_edit_task(
        self,
        task_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentEditTask:
        async with self._lock:
            task = self._document_edit_tasks.get(task_id)
            if task is None:
                raise not_found("Document edit task", task_id)
            if task.user_id != user_id or task.tenant_id != tenant_id:
                raise PlatformError(
                    "PERMISSION_DENIED",
                    "Document edit task ownership does not match the caller",
                    status_code=403,
                )
            return self._copy(task)

    async def list_document_edit_tasks(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        document_name: str | None = None,
    ) -> list[DocumentEditTask]:
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._document_edit_tasks.values()
                if (user_id is None or item.user_id == user_id)
                and (tenant_id is None or item.tenant_id == tenant_id)
                and (document_name is None or item.document_name == document_name)
            ]
        return sorted(values, key=lambda item: item.created_at, reverse=True)

    async def put_document_edit_task(
        self,
        task: DocumentEditTask,
        *,
        expected_version: int,
    ) -> DocumentEditTask:
        async with self._lock:
            current = self._document_edit_tasks.get(task.id)
            if current is None:
                raise not_found("Document edit task", task.id)
            if current.version != expected_version:
                raise conflict("Document edit task changed. Reload it before retrying.")
            updated = task.model_copy(
                update={"version": expected_version + 1, "updated_at": datetime.now(UTC)},
                deep=True,
            )
            self._document_edit_tasks[task.id] = updated
            await self._save_record(DOCUMENT_EDIT_TASKS_RESOURCE, task.id, updated)
        return self._copy(updated)

    async def put_scheduled_aina_task(self, task: ScheduledAinaTask) -> ScheduledAinaTask:
        async with self._lock:
            self._scheduled_aina_tasks[task.id] = task
            await self._save_record(SCHEDULED_AINA_TASKS_RESOURCE, task.id, task)
        return self._copy(task)

    async def list_scheduled_aina_tasks(self) -> list[ScheduledAinaTask]:
        async with self._lock:
            return [self._copy(item) for item in self._scheduled_aina_tasks.values()]

    async def get_scheduled_aina_task(self, task_id: str) -> ScheduledAinaTask:
        async with self._lock:
            task = self._scheduled_aina_tasks.get(task_id)
            if task is None:
                raise not_found("Scheduled AINA task", task_id)
            return self._copy(task)

    async def put_scheduled_aina_execution(
        self, execution: ScheduledAinaExecution
    ) -> ScheduledAinaExecution:
        async with self._lock:
            self._scheduled_aina_executions[execution.id] = execution
            await self._save_record(
                SCHEDULED_AINA_EXECUTIONS_RESOURCE,
                execution.id,
                execution,
            )
        return self._copy(execution)

    async def list_scheduled_aina_executions(
        self, task_id: str, *, limit: int = 50
    ) -> list[ScheduledAinaExecution]:
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._scheduled_aina_executions.values()
                if item.task_id == task_id
            ]
        return sorted(values, key=lambda item: item.started_at, reverse=True)[:limit]

    async def update_scheduled_aina_task(
        self, task_id: str, data: ScheduledAinaTaskUpdate
    ) -> ScheduledAinaTask:
        async with self._lock:
            task = self._scheduled_aina_tasks.get(task_id)
            if task is None:
                raise not_found("Scheduled AINA task", task_id)
            changes = data.model_dump(exclude_none=True)
            if data.prompt is not None:
                changes["input"] = {"message": data.prompt}
            now = datetime.now(UTC)
            changes["updated_at"] = now
            updated = ScheduledAinaTask.model_validate({**task.model_dump(), **changes})
            schedule_fields = {"schedule_type", "interval_seconds", "cron_expression", "timezone"}
            reenabled = data.enabled is True and not task.enabled
            if schedule_fields.intersection(changes) or reenabled:
                updated = updated.model_copy(update={"next_run_at": next_scheduled_run(updated, after=now)})
            self._scheduled_aina_tasks[task_id] = updated
            await self._save_record(SCHEDULED_AINA_TASKS_RESOURCE, task_id, updated)
            return self._copy(updated)

    async def remove_scheduled_aina_task(self, task_id: str) -> None:
        async with self._lock:
            if self._scheduled_aina_tasks.pop(task_id, None) is None:
                raise not_found("Scheduled AINA task", task_id)
            await self._delete_record(SCHEDULED_AINA_TASKS_RESOURCE, task_id)
            execution_ids = [
                item.id
                for item in self._scheduled_aina_executions.values()
                if item.task_id == task_id
            ]
            for execution_id in execution_ids:
                del self._scheduled_aina_executions[execution_id]
                await self._delete_record(SCHEDULED_AINA_EXECUTIONS_RESOURCE, execution_id)

    async def create_conversation(self, data: ConversationCreate) -> Conversation:
        conversation = Conversation(id=f"conv_{uuid4().hex}", **data.model_dump())
        async with self._lock:
            self._conversations[conversation.id] = conversation
            await self._save_record(CONVERSATIONS_RESOURCE, conversation.id, conversation)
        return self._copy(conversation)

    async def get_conversation(self, conversation_id: str, *, include_deleted: bool = False) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or (conversation.status == "deleted" and not include_deleted):
                raise not_found("Conversation", conversation_id)
            return self._copy(conversation)

    async def require_conversation_actor(
        self,
        conversation_id: str,
        *,
        user_id: str,
        tenant_id: str,
        include_deleted: bool = False,
    ) -> Conversation:
        conversation = await self.get_conversation(conversation_id, include_deleted=include_deleted)
        if conversation.user_id != user_id or conversation.tenant_id != tenant_id:
            raise PlatformError(
                code="PERMISSION_DENIED",
                message="Conversation ownership does not match the caller",
                status_code=403,
            )
        return conversation

    async def list_conversations(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        category: str | None = None,
    ) -> list[Conversation]:
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._conversations.values()
                if item.status != "deleted"
                and (user_id is None or item.user_id == user_id)
                and (tenant_id is None or item.tenant_id == tenant_id)
                and (category is None or item.category == category)
            ]
        return sorted(values, key=lambda item: item.updated_at, reverse=True)

    async def update_conversation(self, conversation_id: str, data: ConversationUpdate) -> Conversation:
        changes = data.model_dump(exclude_none=True)
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == "deleted":
                raise not_found("Conversation", conversation_id)
            updated = conversation.model_copy(update={**changes, "updated_at": datetime.now(UTC)}, deep=True)
            self._conversations[conversation_id] = updated
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, updated)
            return self._copy(updated)

    async def set_conversation_status(self, conversation_id: str, status: str) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                raise not_found("Conversation", conversation_id)
            updated = conversation.model_copy(update={"status": status, "updated_at": datetime.now(UTC)}, deep=True)
            self._conversations[conversation_id] = updated
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, updated)
            return self._copy(updated)

    async def start_conversation_run(self, conversation_id: str, trace_id: str) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == "deleted":
                raise not_found("Conversation", conversation_id)
            if conversation.run_status == "running":
                raise conflict("This conversation already has a running request")
            updated = conversation.model_copy(
                update={
                    "run_status": "running",
                    "active_trace_id": trace_id,
                    "run_error": None,
                    "run_started_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                },
                deep=True,
            )
            self._conversations[conversation_id] = updated
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, updated)
            return self._copy(updated)

    async def finish_conversation_run(
        self,
        conversation_id: str,
        *,
        status: str = "idle",
        error: str | None = None,
    ) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                raise not_found("Conversation", conversation_id)
            updated = conversation.model_copy(
                update={
                    "run_status": status,
                    "active_trace_id": None,
                    "run_error": error,
                    "run_started_at": None,
                    "updated_at": datetime.now(UTC),
                },
                deep=True,
            )
            self._conversations[conversation_id] = updated
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, updated)
            return self._copy(updated)

    async def bind_conversation_aina(
        self,
        conversation_id: str,
        aina_id: str,
        *,
        make_primary: bool = False,
        mark_used: bool = False,
    ) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == "deleted":
                raise not_found("Conversation", conversation_id)
            active_aina_ids = list(conversation.active_aina_ids)
            if aina_id not in active_aina_ids:
                active_aina_ids.append(aina_id)
            changes: dict[str, Any] = {
                "active_aina_ids": active_aina_ids,
                "updated_at": datetime.now(UTC),
            }
            if make_primary or conversation.primary_aina_id is None:
                changes["primary_aina_id"] = aina_id
            if mark_used:
                changes["last_aina_id"] = aina_id
            updated = conversation.model_copy(update=changes, deep=True)
            self._conversations[conversation_id] = updated
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, updated)
            return self._copy(updated)

    async def reconcile_conversation_run(self, conversation_id: str) -> Conversation:
        conversation = await self.get_conversation(conversation_id)
        if conversation.run_status != "running":
            return conversation
        if conversation.active_trace_id is None:
            return await self.finish_conversation_run(
                conversation_id,
                status="failed",
                error=INTERRUPTED_RUN_ERROR,
            )
        try:
            trace = await self.get_trace(conversation.active_trace_id)
        except PlatformError as exc:
            if exc.code != "RESOURCE_NOT_FOUND":
                raise
            return await self.finish_conversation_run(
                conversation_id,
                status="failed",
                error=INTERRUPTED_RUN_ERROR,
            )
        if trace.status == "running":
            return conversation
        status = "idle" if trace.status == "completed" else trace.status
        return await self.finish_conversation_run(
            conversation_id,
            status=status,
            error=INTERRUPTED_RUN_ERROR if status == "failed" else None,
        )

    async def create_memory(self, data: MemoryCreate) -> MemoryRecord:
        normalized = data.content.casefold()
        async with self._lock:
            for memory in self._memories.values():
                if (
                    memory.user_id == data.user_id
                    and memory.tenant_id == data.tenant_id
                    and memory.content.casefold() == normalized
                ):
                    return self._copy(memory)
            actor_count = sum(
                item.user_id == data.user_id and item.tenant_id == data.tenant_id
                for item in self._memories.values()
            )
            if actor_count >= 500:
                raise conflict("Memory limit reached; remove or consolidate an existing memory")
            memory = MemoryRecord(id=f"mem_{uuid4().hex}", **data.model_dump())
            self._memories[memory.id] = memory
            await self._save_record(MEMORIES_RESOURCE, memory.id, memory)
            return self._copy(memory)

    async def get_memory(self, memory_id: str, *, user_id: str, tenant_id: str) -> MemoryRecord:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None:
                raise not_found("Memory", memory_id)
            if memory.user_id != user_id or memory.tenant_id != tenant_id:
                raise PlatformError("PERMISSION_DENIED", "Memory ownership does not match the caller", status_code=403)
            return self._copy(memory)

    async def list_memories(
        self,
        *,
        user_id: str,
        tenant_id: str,
        query: str | None = None,
        category: str | None = None,
    ) -> list[MemoryRecord]:
        normalized_query = (query or "").strip().casefold()
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._memories.values()
                if item.user_id == user_id
                and item.tenant_id == tenant_id
                and (category is None or item.category == category)
                and (not normalized_query or normalized_query in item.content.casefold())
            ]
        return sorted(values, key=lambda item: item.updated_at, reverse=True)

    async def search_memories(
        self,
        query: str,
        *,
        user_id: str,
        tenant_id: str,
        limit: int = 8,
    ) -> list[MemoryRecord]:
        candidates = await self.list_memories(user_id=user_id, tenant_id=tenant_id)
        query_terms = _memory_terms(query)
        normalized_query = query.casefold().strip()
        scored: list[tuple[float, MemoryRecord]] = []
        for memory in candidates:
            normalized_content = memory.content.casefold()
            content_terms = _memory_terms(memory.content)
            overlap = len(query_terms & content_terms)
            score = overlap / max(len(query_terms), 1)
            if normalized_query and (
                normalized_query in normalized_content or normalized_content in normalized_query
            ):
                score += 2.0
            if score > 0:
                scored.append((score, memory))
        scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return [memory for _score, memory in scored[:limit]]

    async def update_memory(self, memory_id: str, data: MemoryUpdate) -> MemoryRecord:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None:
                raise not_found("Memory", memory_id)
            if memory.user_id != data.user_id or memory.tenant_id != data.tenant_id:
                raise PlatformError("PERMISSION_DENIED", "Memory ownership does not match the caller", status_code=403)
            changes = data.model_dump(exclude_none=True, exclude={"user_id", "tenant_id"})
            updated = memory.model_copy(update={**changes, "updated_at": datetime.now(UTC)}, deep=True)
            self._memories[memory_id] = updated
            await self._save_record(MEMORIES_RESOURCE, memory_id, updated)
            return self._copy(updated)

    async def remove_memory(self, memory_id: str, *, user_id: str, tenant_id: str) -> None:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None:
                raise not_found("Memory", memory_id)
            if memory.user_id != user_id or memory.tenant_id != tenant_id:
                raise PlatformError("PERMISSION_DENIED", "Memory ownership does not match the caller", status_code=403)
            del self._memories[memory_id]
            await self._delete_record(MEMORIES_RESOURCE, memory_id)

    async def memory_stats(self, *, user_id: str, tenant_id: str) -> MemoryStats:
        memories = await self.list_memories(user_id=user_id, tenant_id=tenant_id)
        counts = {category: 0 for category in ("fact", "preference", "goal", "instruction")}
        for memory in memories:
            counts[memory.category] += 1
        return MemoryStats(total=len(memories), **counts)

    async def append_provider_messages(
        self,
        conversation_id: str,
        messages: Iterable[dict[str, Any]],
        *,
        trace_id: str,
    ) -> list[Message]:
        appended: list[Message] = []
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status != "active":
                raise not_found("Conversation", conversation_id)
            for raw in messages:
                message = Message(
                    id=f"msg_{uuid4().hex}",
                    role=raw["role"],
                    content=raw.get("content") or "",
                    tool_calls=raw.get("tool_calls"),
                    tool_call_id=raw.get("tool_call_id"),
                    name=raw.get("name"),
                    widgets=raw.get("widgets") or [],
                    content_type=(
                        "tool" if raw["role"] == "tool" else "widget" if raw.get("widgets") else "text"
                    ),
                    trace_id=trace_id,
                )
                conversation.messages.append(message)
                appended.append(self._copy(message))
            conversation.updated_at = datetime.now(UTC)
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, conversation)
        return appended

    async def close_dangling_tool_calls(self, conversation_id: str, *, trace_id: str) -> None:
        conversation = await self.get_conversation(conversation_id)
        if not conversation.messages:
            return
        last = conversation.messages[-1]
        if last.role != "assistant" or not last.tool_calls:
            return
        closing = [
            {
                "role": "tool",
                "name": call.get("function", {}).get("name", "unknown"),
                "tool_call_id": call.get("id"),
                "content": "Cancelled because the user started a new turn before granting approval.",
            }
            for call in last.tool_calls
        ]
        await self.append_provider_messages(conversation_id, closing, trace_id=trace_id)
        await self.cancel_pending_approvals(conversation_id)

    async def register_tool(self, tool: ToolRecord) -> ToolRecord:
        async with self._lock:
            if tool.tool_id in self._tools:
                raise conflict(f"Tool {tool.tool_id!r} is already registered")
            self._tools[tool.tool_id] = tool
            await self._save_record(TOOLS_RESOURCE, tool.tool_id, tool)
        return self._copy(tool)

    async def get_tool(self, tool_id: str) -> ToolRecord:
        async with self._lock:
            tool = self._tools.get(tool_id)
            if tool is None:
                raise not_found("Tool", tool_id)
            return self._copy(tool)

    async def list_tools(self) -> list[ToolRecord]:
        async with self._lock:
            return [self._copy(item) for item in self._tools.values()]

    async def remove_tool(self, tool_id: str) -> None:
        async with self._lock:
            if self._tools.pop(tool_id, None) is None:
                raise not_found("Tool", tool_id)
            await self._delete_record(TOOLS_RESOURCE, tool_id)

    async def register_skill(self, skill: SkillRecord) -> SkillRecord:
        async with self._lock:
            if skill.skill_id in self._skills:
                raise conflict(f"Skill {skill.skill_id!r} is already registered")
            self._skills[skill.skill_id] = skill
            await self._save_record(SKILLS_RESOURCE, skill.skill_id, skill)
        return self._copy(skill)

    async def get_skill(self, skill_id: str) -> SkillRecord:
        async with self._lock:
            skill = self._skills.get(skill_id)
            if skill is None:
                raise not_found("Skill", skill_id)
            return self._copy(skill)

    async def list_skills(self) -> list[SkillRecord]:
        async with self._lock:
            return [self._copy(item) for item in self._skills.values()]

    async def remove_skill(self, skill_id: str) -> None:
        async with self._lock:
            if self._skills.pop(skill_id, None) is None:
                raise not_found("Skill", skill_id)
            await self._delete_record(SKILLS_RESOURCE, skill_id)

    async def register_aina(self, aina: AinaRecord) -> AinaRecord:
        aina_id = aina.manifest.aina.id
        async with self._lock:
            if aina_id in self._ainas:
                raise conflict(f"AINA {aina_id!r} is already registered")
            self._ainas[aina_id] = aina
            await self._save_record(AINAS_RESOURCE, aina_id, aina)
        return self._copy(aina)

    async def upsert_aina(self, aina: AinaRecord) -> AinaRecord:
        aina_id = aina.manifest.aina.id
        async with self._lock:
            self._ainas[aina_id] = aina
            await self._save_record(AINAS_RESOURCE, aina_id, aina)
        return self._copy(aina)

    async def get_aina(self, aina_id: str) -> AinaRecord:
        async with self._lock:
            aina = self._ainas.get(aina_id)
            if aina is None:
                raise not_found("AINA", aina_id)
            return self._copy(aina)

    async def list_ainas(self) -> list[AinaRecord]:
        async with self._lock:
            return [self._copy(item) for item in self._ainas.values()]

    async def remove_aina(self, aina_id: str) -> None:
        async with self._lock:
            if self._ainas.pop(aina_id, None) is None:
                raise not_found("AINA", aina_id)
            await self._delete_record(AINAS_RESOURCE, aina_id)
            for key in [key for key in self._installations if key[2] == aina_id]:
                del self._installations[key]
                await self._delete_record(INSTALLATIONS_RESOURCE, _installation_record_id(*key))

    async def put_installation(self, installation: AinaInstallation) -> AinaInstallation:
        key = (installation.tenant_id, installation.user_id, installation.aina_id)
        async with self._lock:
            self._installations[key] = installation
            await self._save_record(INSTALLATIONS_RESOURCE, _installation_record_id(*key), installation)
        return self._copy(installation)

    async def get_installation(self, *, tenant_id: str, user_id: str, aina_id: str) -> AinaInstallation:
        async with self._lock:
            installation = self._installations.get((tenant_id, user_id, aina_id))
            if installation is None:
                raise not_found("AINA installation", aina_id)
            return self._copy(installation)

    async def list_installations(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> list[AinaInstallation]:
        async with self._lock:
            return [
                self._copy(item)
                for item in self._installations.values()
                if (tenant_id is None or item.tenant_id == tenant_id) and (user_id is None or item.user_id == user_id)
            ]

    async def remove_installation(self, *, tenant_id: str, user_id: str, aina_id: str) -> None:
        async with self._lock:
            if self._installations.pop((tenant_id, user_id, aina_id), None) is None:
                raise not_found("AINA installation", aina_id)
            await self._delete_record(
                INSTALLATIONS_RESOURCE,
                _installation_record_id(tenant_id, user_id, aina_id),
            )

    async def create_trace(self, trace: TraceRecord) -> TraceRecord:
        async with self._lock:
            self._traces[trace.trace_id] = trace
            await self._save_record(TRACES_RESOURCE, trace.trace_id, trace)
        return self._copy(trace)

    async def add_trace_event(self, trace_id: str, event: TraceEvent) -> None:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise not_found("Trace", trace_id)
            trace.events.append(event)
            await self._save_record(TRACES_RESOURCE, trace_id, trace)

    async def finish_trace(self, trace_id: str, status: str) -> TraceRecord:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise not_found("Trace", trace_id)
            trace.status = status  # type: ignore[assignment]
            trace.completed_at = datetime.now(UTC)
            await self._save_record(TRACES_RESOURCE, trace_id, trace)
            return self._copy(trace)

    async def get_trace(self, trace_id: str) -> TraceRecord:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise not_found("Trace", trace_id)
            return self._copy(trace)

    async def list_traces(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[TraceRecord]:
        async with self._lock:
            traces = [
                self._copy(item)
                for item in self._traces.values()
                if (user_id is None or item.user_id == user_id) and (tenant_id is None or item.tenant_id == tenant_id)
            ]
        return sorted(traces, key=lambda item: item.created_at, reverse=True)

    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        async with self._lock:
            self._approvals[approval.id] = approval
            await self._save_record(APPROVALS_RESOURCE, approval.id, approval)
        return self._copy(approval)

    async def get_approval(self, approval_id: str) -> ApprovalRecord:
        async with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise not_found("Approval", approval_id)
            return self._copy(approval)

    async def list_approvals(
        self,
        *,
        conversation_id: str | None = None,
        user_id: str | None = None,
        tenant_id: str | None = None,
        status: str | None = None,
    ) -> list[ApprovalRecord]:
        async with self._lock:
            approvals = [
                self._copy(item)
                for item in self._approvals.values()
                if (conversation_id is None or item.conversation_id == conversation_id)
                and (user_id is None or item.user_id == user_id)
                and (tenant_id is None or item.tenant_id == tenant_id)
                and (status is None or item.status == status)
            ]
        return sorted(approvals, key=lambda item: item.created_at, reverse=True)

    async def set_approval_status(self, approval_id: str, status: str) -> ApprovalRecord:
        async with self._lock:
            approval = self._approvals.get(approval_id)
            if approval is None:
                raise not_found("Approval", approval_id)
            approval.status = status  # type: ignore[assignment]
            approval.resolved_at = datetime.now(UTC)
            await self._save_record(APPROVALS_RESOURCE, approval_id, approval)
            return self._copy(approval)

    async def cancel_pending_approvals(self, conversation_id: str) -> None:
        async with self._lock:
            for approval in self._approvals.values():
                if approval.conversation_id == conversation_id and approval.status == "pending":
                    approval.status = "denied"
                    approval.resolved_at = datetime.now(UTC)
                    await self._save_record(APPROVALS_RESOURCE, approval.id, approval)


def _installation_record_id(tenant_id: str, user_id: str, aina_id: str) -> str:
    raw = json.dumps([tenant_id, user_id, aina_id], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _memory_terms(value: str) -> set[str]:
    normalized = value.casefold()
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    cjk = re.findall(r"[\u3400-\u9fff]", normalized)
    words.update(cjk)
    words.update("".join(cjk[index : index + 2]) for index in range(len(cjk) - 1))
    return words
