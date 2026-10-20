from __future__ import annotations

import asyncio
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from collections.abc import Callable
from typing import Any, Iterable
from uuid import uuid4

from tianzhou_agent_platform.auth.models import DeveloperToken, OidcProvider, UserRecord
from tianzhou_agent_platform.aina.package.storage import AinaStorageItem, check_quota
from tianzhou_agent_platform.aina.package.tasks import AinaTask
from tianzhou_agent_platform.aina.project import AinaProjectRecord
from tianzhou_agent_platform.aina.memory.models import MemoryCreate, MemoryRecord, MemoryStats, MemoryUpdate
from tianzhou_agent_platform.aina.document.task_models import DocumentEditTask
from tianzhou_agent_platform.aina.package.files import AinaFile, file_record_id
from tianzhou_agent_platform.conversations.attachments import Attachment
from tianzhou_agent_platform.aina.package.notifications import KEEP_PER_USER, AinaNotification
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
from tianzhou_agent_platform.conversations.models import (
    Conversation,
    ConversationCreate,
    ConversationUpdate,
    Message,
)
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord
from tianzhou_agent_platform.core.errors import PlatformError, conflict, not_found
from tianzhou_agent_platform.core.feedback import FeedbackHistoryItem, FeedbackRecord
from tianzhou_agent_platform.model_providers.models import (
    ModelDefinition,
    ModelProviderCreate,
    ModelProviderRecord,
    ModelProviderUpdate,
    ModelRuntimeConfig,
)
from tianzhou_agent_platform.observability.models import (
    LLMCallRecord,
    TraceEvent,
    TraceRecord,
    TraceSpan,
)
from tianzhou_agent_platform.core.workspace import Workspace, WorkspaceCreate, WorkspaceUpdate
from tianzhou_agent_platform.sandbox.models import SandboxExecution, SandboxRecord

WORKSPACES_RESOURCE = "workspaces"
CONVERSATIONS_RESOURCE = "conversations"
MEMORIES_RESOURCE = "memories"
TOOLS_RESOURCE = "tools"
SKILLS_RESOURCE = "skills"
AINAS_RESOURCE = "ainas"
AINA_PROJECTS_RESOURCE = "aina_projects"
INSTALLATIONS_RESOURCE = "installations"
TRACES_RESOURCE = "traces"
LLM_CALLS_RESOURCE = "llm_calls"
APPROVALS_RESOURCE = "approvals"
MODEL_PROVIDERS_RESOURCE = "model_providers"
SCHEDULED_AINA_TASKS_RESOURCE = "scheduled_aina_tasks"
INTERRUPTED_RUN_ERROR = "上一次处理未正常结束，请重新发送请求。"
SCHEDULED_AINA_EXECUTIONS_RESOURCE = "scheduled_aina_executions"
OBS_TRACE_VISIBILITY_GRACE = timedelta(minutes=15)
# A confirmed approval resumes its paused run under the same trace, which keeps reading approval_required.
RUN_IN_PROGRESS_TRACE_STATUSES = frozenset({"running", "approval_required"})
DOCUMENT_EDIT_TASKS_RESOURCE = "document_edit_tasks"
SANDBOXES_RESOURCE = "sandboxes"
SANDBOX_EXECUTIONS_RESOURCE = "sandbox_executions"
USERS_RESOURCE = "users"
FEEDBACKS_RESOURCE = "feedbacks"


class InMemoryRepository:
    """Concurrency-safe MVP repository.

    All runtime code depends on this small method surface rather than on the
    dictionaries directly, so a durable SQL-backed implementation can replace
    it without changing the agent or API contracts.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._workspaces: dict[str, Workspace] = {}
        self._conversations: dict[str, Conversation] = {}
        self._tools: dict[str, ToolRecord] = {}
        self._skills: dict[str, SkillRecord] = {}
        self._ainas: dict[str, AinaRecord] = {}
        self._aina_projects: dict[str, AinaProjectRecord] = {}
        self._installations: dict[tuple[str, str, str], AinaInstallation] = {}
        self._traces: dict[str, TraceRecord] = {}
        self._llm_calls: dict[str, LLMCallRecord] = {}
        self._approvals: dict[str, ApprovalRecord] = {}
        self._memories: dict[str, MemoryRecord] = {}
        self._model_providers: dict[str, ModelProviderRecord] = {}
        self._scheduled_aina_tasks: dict[str, ScheduledAinaTask] = {}
        self._scheduled_aina_executions: dict[str, ScheduledAinaExecution] = {}
        self._scheduled_aina_claims: set[str] = set()
        self._document_edit_tasks: dict[str, DocumentEditTask] = {}
        self._sandboxes: dict[str, SandboxRecord] = {}
        self._sandbox_executions: dict[str, SandboxExecution] = {}
        self._users: dict[str, UserRecord] = {}
        self._feedbacks: dict[str, FeedbackRecord] = {}
        self._aina_storage: dict[str, AinaStorageItem] = {}
        self._developer_tokens: dict[str, DeveloperToken] = {}
        self._aina_notifications: dict[str, AinaNotification] = {}
        self._aina_files: dict[str, AinaFile] = {}
        self._chat_attachments: dict[str, Attachment] = {}
        self._aina_tasks: dict[str, AinaTask] = {}

    async def _save_record(self, resource: str, record_id: str, value: Any) -> None:
        return None

    async def _delete_record(self, resource: str, record_id: str) -> None:
        return None

    @staticmethod
    def _copy[T](value: T) -> T:
        if hasattr(value, "model_copy"):
            return value.model_copy(deep=True)  # type: ignore[no-any-return, union-attr]
        return value

    async def create_user(self, user: UserRecord) -> UserRecord:
        async with self._lock:
            email = normalized_email(str(user.email))
            if any(normalized_email(str(item.email)) == email for item in self._users.values()):
                raise email_registered()
            if user.github_id and any(item.github_id == user.github_id for item in self._users.values()):
                raise github_already_linked()
            if user.huawei_id and any(item.huawei_id == user.huawei_id for item in self._users.values()):
                raise huawei_already_linked()
            for provider, identity in user.oidc_identities.items():
                if any(item.oidc_identities.get(provider) == identity for item in self._users.values()):
                    raise oidc_already_linked(provider)
            self._users[user.id] = user
            await self._save_record(USERS_RESOURCE, user.id, user)
            return self._copy(user)

    async def find_user_by_id(self, user_id: str) -> UserRecord | None:
        async with self._lock:
            user = self._users.get(user_id)
            return self._copy(user) if user else None

    async def find_user_by_email(self, email: str) -> UserRecord | None:
        normalized = normalized_email(email)
        async with self._lock:
            user = next(
                (item for item in self._users.values() if normalized_email(str(item.email)) == normalized),
                None,
            )
            return self._copy(user) if user else None

    async def list_users(
        self,
        *,
        query: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[UserRecord]:
        normalized = (query or "").strip().lower()
        async with self._lock:
            users = list(self._users.values())
        if normalized:
            users = [
                user
                for user in users
                if normalized in user.id.lower()
                or normalized in user.name.lower()
                or normalized in str(user.email).lower()
                or normalized in user.tenant_id.lower()
            ]
        users.sort(key=lambda user: (user.created_at, user.id), reverse=True)
        start = max(0, offset)
        return [self._copy(user) for user in users[start : start + max(1, limit)]]

    async def upsert_feedback(self, feedback: FeedbackRecord) -> FeedbackRecord:
        async with self._lock:
            existing = next(
                (
                    item
                    for item in self._feedbacks.values()
                    if item.user_id == feedback.user_id
                    and item.tenant_id == feedback.tenant_id
                    and item.message_id == feedback.message_id
                ),
                None,
            )
            stored = feedback if existing is None else resubmitted_feedback(existing, feedback)
            self._feedbacks[stored.id] = stored
            await self._save_record(FEEDBACKS_RESOURCE, stored.id, stored)
            return self._copy(stored)

    async def get_feedback_for_message(
        self,
        message_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> FeedbackRecord | None:
        async with self._lock:
            item = next(
                (
                    feedback
                    for feedback in self._feedbacks.values()
                    if feedback.active
                    and feedback.message_id == message_id
                    and feedback.user_id == user_id
                    and feedback.tenant_id == tenant_id
                ),
                None,
            )
            return self._copy(item) if item else None

    async def get_feedback(self, feedback_id: str) -> FeedbackRecord:
        async with self._lock:
            item = self._feedbacks.get(feedback_id)
            if item is None or not item.active:
                raise not_found("Feedback", feedback_id)
            return self._copy(item)

    async def list_feedbacks(
        self,
        *,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        rating: str | None = None,
        user_query: str | None = None,
    ) -> list[FeedbackRecord]:
        normalized = (user_query or "").strip().lower()
        async with self._lock:
            items = [
                self._copy(item)
                for item in self._feedbacks.values()
                if item.active
                and (from_at is None or item.created_at >= from_at)
                and (to_at is None or item.created_at < to_at)
                and (rating is None or item.rating == rating)
                and (
                    not normalized
                    or normalized in item.user_name.lower()
                    or normalized in item.user_email.lower()
                )
            ]
        return sorted(items, key=lambda item: item.created_at, reverse=True)

    async def cancel_feedback(
        self,
        message_id: str,
        *,
        user_id: str,
        tenant_id: str,
        actor_name: str,
    ) -> None:
        async with self._lock:
            item = next(
                (
                    feedback
                    for feedback in self._feedbacks.values()
                    if feedback.active
                    and feedback.message_id == message_id
                    and feedback.user_id == user_id
                    and feedback.tenant_id == tenant_id
                ),
                None,
            )
            if item is None:
                return
            updated = cancelled_feedback(item, user_id=user_id, actor_name=actor_name)
            self._feedbacks[item.id] = updated
            await self._save_record(FEEDBACKS_RESOURCE, item.id, updated)

    async def update_feedback_case(
        self,
        feedback_id: str,
        *,
        status: str,
        assignee: str,
        conclusion: str,
        actor_id: str,
        actor_name: str,
    ) -> FeedbackRecord:
        async with self._lock:
            item = self._feedbacks.get(feedback_id)
            if item is None or not item.active:
                raise not_found("Feedback", feedback_id)
            updated = updated_feedback_case(
                item, status=status, assignee=assignee, conclusion=conclusion, actor_id=actor_id, actor_name=actor_name
            )
            self._feedbacks[item.id] = updated
            await self._save_record(FEEDBACKS_RESOURCE, item.id, updated)
            return self._copy(updated)

    async def upsert_github_user(
        self,
        *,
        github_id: str,
        github_login: str,
        email: str,
        name: str,
        avatar_url: str | None,
    ) -> UserRecord:
        email = normalized_email(email)
        async with self._lock:
            user = next((item for item in self._users.values() if item.github_id == github_id), None)
            if user is None:
                if any(normalized_email(str(item.email)) == email for item in self._users.values()):
                    raise github_email_registered()
                user = new_github_user(
                    github_id=github_id, github_login=github_login, email=email, name=name, avatar_url=avatar_url
                )
            else:
                user = relinked_github_user(user, github_login=github_login, name=name, avatar_url=avatar_url)
            self._users[user.id] = user
            await self._save_record(USERS_RESOURCE, user.id, user)
            return self._copy(user)

    async def upsert_huawei_user(self, *, huawei_id: str, email: str, name: str) -> UserRecord:
        email = normalized_email(email)
        async with self._lock:
            user = next((item for item in self._users.values() if item.huawei_id == huawei_id), None)
            if user is None:
                if any(normalized_email(str(item.email)) == email for item in self._users.values()):
                    raise linked_email_registered("Huawei")
                user = new_huawei_user(huawei_id=huawei_id, email=email, name=name)
            else:
                user = relinked_huawei_user(user, name=name)
            self._users[user.id] = user
            await self._save_record(USERS_RESOURCE, user.id, user)
            return self._copy(user)

    async def upsert_oidc_user(
        self, *, provider: OidcProvider, identity: str, email: str, name: str | None, avatar_url: str | None
    ) -> UserRecord:
        email = normalized_email(email)
        async with self._lock:
            user = next(
                (item for item in self._users.values() if item.oidc_identities.get(provider) == identity), None
            )
            if user is None:
                if any(normalized_email(str(item.email)) == email for item in self._users.values()):
                    raise linked_email_registered(provider)
                user = new_oidc_user(provider=provider, identity=identity, email=email, name=name, avatar_url=avatar_url)
            else:
                user = relinked_oidc_user(user, name=name, avatar_url=avatar_url)
            self._users[user.id] = user
            await self._save_record(USERS_RESOURCE, user.id, user)
            return self._copy(user)

    async def create_model_provider(self, data: ModelProviderCreate) -> ModelProviderRecord:
        async with self._lock:
            actor_count = sum(
                item.user_id == data.user_id and item.tenant_id == data.tenant_id
                for item in self._model_providers.values()
            )
            provider = new_model_provider(data, existing_count=actor_count)
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
            require_owner(provider, user_id=user_id, tenant_id=tenant_id, label="Model provider")
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
            updated = updated_model_provider(provider, data)
            self._model_providers[provider_id] = updated
            await self._save_record(MODEL_PROVIDERS_RESOURCE, provider_id, updated)
            return self._copy(updated)

    async def remove_model_provider(self, provider_id: str, *, user_id: str, tenant_id: str) -> None:
        async with self._lock:
            provider = self._model_providers.get(provider_id)
            if provider is None:
                raise not_found("Model provider", provider_id)
            require_owner(provider, user_id=user_id, tenant_id=tenant_id, label="Model provider")
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
            require_owner(provider, user_id=user_id, tenant_id=tenant_id, label="Model provider")
            actor_providers = [
                item
                for item in self._model_providers.values()
                if item.user_id == user_id and item.tenant_id == tenant_id
            ]
            changed = providers_with_default_model(actor_providers, provider_id, model_id)
            for item in changed:
                self._model_providers[item.id] = item
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
            return default_model_runtime(
                item
                for item in self._model_providers.values()
                if item.user_id == user_id and item.tenant_id == tenant_id
            )

    async def create_scheduled_aina_task(self, data: ScheduledAinaTaskCreate) -> ScheduledAinaTask:
        task = new_scheduled_aina_task(data)
        async with self._lock:
            self._scheduled_aina_tasks[task.id] = task
            await self._save_record(SCHEDULED_AINA_TASKS_RESOURCE, task.id, task)
        return self._copy(task)

    async def put_sandbox(self, sandbox: SandboxRecord) -> SandboxRecord:
        async with self._lock:
            existing = next(
                (
                    item
                    for item in self._sandboxes.values()
                    if item.user_id == sandbox.user_id
                    and item.tenant_id == sandbox.tenant_id
                    and item.workspace_id == sandbox.workspace_id
                    and item.id != sandbox.id
                ),
                None,
            )
            if existing is not None:
                raise conflict("The sandbox scope already owns a sandbox")
            self._sandboxes[sandbox.id] = sandbox
            await self._save_record(SANDBOXES_RESOURCE, sandbox.id, sandbox)
            return self._copy(sandbox)

    async def get_sandbox_for_actor(
        self,
        *,
        user_id: str,
        tenant_id: str,
        workspace_id: str | None = None,
    ) -> SandboxRecord:
        async with self._lock:
            sandbox = next(
                (
                    item
                    for item in self._sandboxes.values()
                    if item.user_id == user_id
                    and item.tenant_id == tenant_id
                    and item.workspace_id == workspace_id
                ),
                None,
            )
            if sandbox is None:
                scope = workspace_id or "standalone"
                raise not_found("Sandbox", f"{tenant_id}/{user_id}/{scope}")
            return self._copy(sandbox)

    async def put_sandbox_execution(self, execution: SandboxExecution) -> SandboxExecution:
        async with self._lock:
            self._sandbox_executions[execution.id] = execution
            await self._save_record(SANDBOX_EXECUTIONS_RESOURCE, execution.id, execution)
            return self._copy(execution)

    async def list_sandbox_executions(
        self,
        *,
        user_id: str,
        tenant_id: str,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[SandboxExecution]:
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._sandbox_executions.values()
                if item.user_id == user_id
                and item.tenant_id == tenant_id
                and item.workspace_id == workspace_id
            ]
        return sorted(values, key=lambda item: item.started_at, reverse=True)[:limit]

    async def remove_sandbox(self, sandbox_id: str) -> None:
        async with self._lock:
            sandbox = self._sandboxes.pop(sandbox_id, None)
            if sandbox is None:
                raise not_found("Sandbox", sandbox_id)
            await self._delete_record(SANDBOXES_RESOURCE, sandbox_id)
            execution_ids = [
                item.id for item in self._sandbox_executions.values() if item.sandbox_id == sandbox_id
            ]
            for execution_id in execution_ids:
                del self._sandbox_executions[execution_id]
                await self._delete_record(SANDBOX_EXECUTIONS_RESOURCE, execution_id)

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
            return self._copy(require_owner(task, user_id=user_id, tenant_id=tenant_id, label="Document edit task"))

    async def list_document_edit_tasks(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        document_name: str | None = None,
        workspace_id: str | None = None,
    ) -> list[DocumentEditTask]:
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._document_edit_tasks.values()
                if (user_id is None or item.user_id == user_id)
                and (tenant_id is None or item.tenant_id == tenant_id)
                and (document_name is None or item.document_name == document_name)
                and (workspace_id is None or item.workspace_id == workspace_id)
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
                raise document_edit_task_changed()
            updated = next_document_edit_task_version(task, expected_version)
            self._document_edit_tasks[task.id] = updated
            await self._save_record(DOCUMENT_EDIT_TASKS_RESOURCE, task.id, updated)
        return self._copy(updated)

    async def put_scheduled_aina_task(self, task: ScheduledAinaTask) -> ScheduledAinaTask:
        async with self._lock:
            self._scheduled_aina_tasks[task.id] = task
            await self._save_record(SCHEDULED_AINA_TASKS_RESOURCE, task.id, task)
        return self._copy(task)

    async def list_scheduled_aina_tasks(
        self, *, user_id: str | None = None, tenant_id: str | None = None
    ) -> list[ScheduledAinaTask]:
        async with self._lock:
            return [
                self._copy(item)
                for item in self._scheduled_aina_tasks.values()
                if (user_id is None or item.user_id == user_id) and (tenant_id is None or item.tenant_id == tenant_id)
            ]

    async def list_due_scheduled_aina_tasks(self, now: datetime) -> list[ScheduledAinaTask]:
        """Enabled tasks whose next run is at or before ``now``."""
        async with self._lock:
            return [
                self._copy(item)
                for item in self._scheduled_aina_tasks.values()
                if item.enabled and item.next_run_at <= now
            ]

    async def get_scheduled_aina_task(self, task_id: str) -> ScheduledAinaTask:
        async with self._lock:
            task = self._scheduled_aina_tasks.get(task_id)
            if task is None:
                raise not_found("Scheduled AINA task", task_id)
            return self._copy(task)

    async def start_scheduled_aina_run(
        self, task_id: str, *, node_id: str, started_at: datetime, scheduled_for: datetime | None = None
    ) -> ScheduledAinaTask | None:
        """Start a run atomically; skip a deleted, paused or rescheduled occurrence."""
        return await self._record_scheduled_aina_run(
            task_id,
            lambda task: started_scheduled_aina_run(
                task, node_id=node_id, started_at=started_at, scheduled_for=scheduled_for
            ),
        )

    async def finish_scheduled_aina_run(
        self,
        task_id: str,
        *,
        status: str,
        error: str | None,
        result: dict[str, Any] | None,
        finished_at: datetime,
        advance_schedule: bool,
    ) -> ScheduledAinaTask | None:
        """Record a finished run, changing only run-state fields; None when the task was deleted."""
        return await self._record_scheduled_aina_run(
            task_id,
            lambda task: finished_scheduled_aina_run(
                task,
                status=status,
                error=error,
                result=result,
                finished_at=finished_at,
                advance_schedule=advance_schedule,
            ),
        )

    async def _record_scheduled_aina_run(
        self, task_id: str, change: Callable[[ScheduledAinaTask], ScheduledAinaTask | None]
    ) -> ScheduledAinaTask | None:
        async with self._lock:
            task = self._scheduled_aina_tasks.get(task_id)
            if task is None:
                return None
            updated = change(task)
            if updated is None:
                return None
            self._scheduled_aina_tasks[task_id] = updated
            await self._save_record(SCHEDULED_AINA_TASKS_RESOURCE, task_id, updated)
            return self._copy(updated)

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
            updated = updated_scheduled_aina_task(task, data)
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

    async def create_workspace(self, data: WorkspaceCreate) -> Workspace:
        workspace = new_workspace(data)
        async with self._lock:
            self._workspaces[workspace.id] = workspace
            await self._save_record(WORKSPACES_RESOURCE, workspace.id, workspace)
        return self._copy(workspace)

    async def get_workspace(self, workspace_id: str) -> Workspace:
        async with self._lock:
            workspace = self._workspaces.get(workspace_id)
            if workspace is None:
                raise not_found("Workspace", workspace_id)
            return self._copy(workspace)

    async def require_workspace_actor(
        self,
        workspace_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> Workspace:
        workspace = await self.get_workspace(workspace_id)
        if workspace.user_id != user_id or workspace.tenant_id != tenant_id:
            raise PlatformError(
                code="PERMISSION_DENIED",
                message="Workspace ownership does not match the caller",
                status_code=403,
            )
        return workspace

    async def list_workspaces(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[Workspace]:
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._workspaces.values()
                if (user_id is None or item.user_id == user_id)
                and (tenant_id is None or item.tenant_id == tenant_id)
            ]
        return sorted(values, key=lambda item: item.updated_at, reverse=True)

    async def update_workspace(self, workspace_id: str, data: WorkspaceUpdate) -> Workspace:
        async with self._lock:
            workspace = self._workspaces.get(workspace_id)
            if workspace is None:
                raise not_found("Workspace", workspace_id)
            updated = updated_workspace(workspace, data)
            self._workspaces[workspace_id] = updated
            await self._save_record(WORKSPACES_RESOURCE, workspace_id, updated)
            return self._copy(updated)

    async def create_conversation(self, data: ConversationCreate) -> Conversation:
        if data.workspace_id is not None:
            await self.require_workspace_actor(
                data.workspace_id,
                user_id=data.user_id,
                tenant_id=data.tenant_id,
            )
        conversation = new_conversation(data)
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
        workspace_id: str | None = None,
    ) -> list[Conversation]:
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._conversations.values()
                if item.status != "deleted"
                and (user_id is None or item.user_id == user_id)
                and (tenant_id is None or item.tenant_id == tenant_id)
                and (category is None or item.category == category)
                and (workspace_id is None or item.workspace_id == workspace_id)
            ]
        return sorted(values, key=lambda item: item.updated_at, reverse=True)

    async def update_conversation(self, conversation_id: str, data: ConversationUpdate) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == "deleted":
                raise not_found("Conversation", conversation_id)
            updated = updated_conversation(conversation, data)
            self._conversations[conversation_id] = updated
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, updated)
            return self._copy(updated)

    async def set_conversation_status(self, conversation_id: str, status: str) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                raise not_found("Conversation", conversation_id)
            updated = conversation_with_status(conversation, status)
            self._conversations[conversation_id] = updated
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, updated)
            return self._copy(updated)

    async def start_conversation_run(self, conversation_id: str, trace_id: str) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None or conversation.status == "deleted":
                raise not_found("Conversation", conversation_id)
            updated = started_run(conversation, trace_id)
            self._conversations[conversation_id] = updated
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, updated)
            return self._copy(updated)

    async def finish_conversation_run(
        self,
        conversation_id: str,
        *,
        status: str = "idle",
        error: str | None = None,
        expected_trace_id: str | None = None,
    ) -> Conversation:
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                raise not_found("Conversation", conversation_id)
            if expected_trace_id is not None and conversation.active_trace_id != expected_trace_id:
                return self._copy(conversation)
            updated = finished_run(conversation, status=status, error=error)
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
            updated = with_bound_aina(conversation, aina_id, make_primary=make_primary, mark_used=mark_used)
            self._conversations[conversation_id] = updated
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, updated)
            return self._copy(updated)

    async def _has_live_conversation_run(self, conversation: Conversation) -> bool:
        return False

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
            # Phase four: traces live in the OBS pipeline; resolve the real
            # status instead of marking a possibly-finished run as failed.
            resolver = getattr(self, "obs_trace_status_resolver", None)
            if resolver is not None:
                try:
                    status = await resolver(conversation.active_trace_id)
                except Exception:  # noqa: BLE001 - resolver must not break reconciliation
                    status = None
                if status is not None:
                    if status in RUN_IN_PROGRESS_TRACE_STATUSES:
                        return conversation
                    resolved = "idle" if status == "completed" else status
                    return await self.finish_conversation_run(
                        conversation_id,
                        status=resolved,
                        error=INTERRUPTED_RUN_ERROR if resolved == "failed" else None,
                        expected_trace_id=conversation.active_trace_id,
                    )
                # A durably buffered trace may not be queryable from OBS MySQL yet,
                # especially from another node. Keep the business run and its
                # Redis lock intact for the same window as the run-lock TTL;
                # absence is not proof that the producer was interrupted.
                if (
                    conversation.run_started_at is not None
                    and datetime.now(UTC) - conversation.run_started_at
                    < OBS_TRACE_VISIBILITY_GRACE
                ):
                    return conversation
            if await self._has_live_conversation_run(conversation):
                return conversation
            return await self.finish_conversation_run(
                conversation_id,
                status="failed",
                error=INTERRUPTED_RUN_ERROR,
                expected_trace_id=conversation.active_trace_id,
            )
        if trace.status in RUN_IN_PROGRESS_TRACE_STATUSES:
            return conversation
        status = "idle" if trace.status == "completed" else trace.status
        return await self.finish_conversation_run(
            conversation_id,
            status=status,
            error=INTERRUPTED_RUN_ERROR if status == "failed" else None,
            expected_trace_id=conversation.active_trace_id,
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
            memory = new_memory(data, existing_count=actor_count)
            self._memories[memory.id] = memory
            await self._save_record(MEMORIES_RESOURCE, memory.id, memory)
            return self._copy(memory)

    async def get_memory(self, memory_id: str, *, user_id: str, tenant_id: str) -> MemoryRecord:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None:
                raise not_found("Memory", memory_id)
            return self._copy(require_owner(memory, user_id=user_id, tenant_id=tenant_id, label="Memory"))

    async def list_memories(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        query: str | None = None,
        category: str | None = None,
    ) -> list[MemoryRecord]:
        normalized_query = (query or "").strip().casefold()
        async with self._lock:
            values = [
                self._copy(item)
                for item in self._memories.values()
                if (user_id is None or item.user_id == user_id)
                and (tenant_id is None or item.tenant_id == tenant_id)
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
        return ranked_memories(query, await self.list_memories(user_id=user_id, tenant_id=tenant_id), limit=limit)

    async def update_memory(self, memory_id: str, data: MemoryUpdate) -> MemoryRecord:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None:
                raise not_found("Memory", memory_id)
            updated = updated_memory(memory, data)
            self._memories[memory_id] = updated
            await self._save_record(MEMORIES_RESOURCE, memory_id, updated)
            return self._copy(updated)

    async def remove_memory(self, memory_id: str, *, user_id: str, tenant_id: str) -> None:
        async with self._lock:
            memory = self._memories.get(memory_id)
            if memory is None:
                raise not_found("Memory", memory_id)
            require_owner(memory, user_id=user_id, tenant_id=tenant_id, label="Memory")
            del self._memories[memory_id]
            await self._delete_record(MEMORIES_RESOURCE, memory_id)

    async def memory_stats(self, *, user_id: str, tenant_id: str) -> MemoryStats:
        return memory_stats_of(await self.list_memories(user_id=user_id, tenant_id=tenant_id))

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
                message = message_from_provider(raw, trace_id=trace_id)
                conversation.messages.append(message)
                appended.append(self._copy(message))
            conversation.updated_at = datetime.now(UTC)
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, conversation)
        return appended

    async def close_dangling_tool_calls(
        self,
        conversation_id: str,
        *,
        trace_id: str,
    ) -> list[ApprovalRecord]:
        conversation = await self.get_conversation(conversation_id)
        if not conversation.messages:
            return []
        last = conversation.messages[-1]
        if last.role != "assistant" or not last.tool_calls:
            return []
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
        return await self.cancel_pending_approvals(conversation_id)

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

    async def create_aina_project(self, project: AinaProjectRecord) -> AinaProjectRecord:
        async with self._lock:
            for existing in self._aina_projects.values():
                if (
                    existing.tenant_id == project.tenant_id
                    and existing.user_id == project.user_id
                    and existing.manifest.aina.id == project.manifest.aina.id
                    and existing.manifest.aina.version == project.manifest.aina.version
                ):
                    return self._copy(self._resolve_aina_project_import(existing, project))
            if project.id in self._aina_projects:
                raise conflict(f"AINA project {project.id!r} already exists")
            self._aina_projects[project.id] = project
            try:
                await self._save_record(AINA_PROJECTS_RESOURCE, project.id, project)
            except Exception:
                self._aina_projects.pop(project.id, None)
                raise
            return self._copy(project)

    @staticmethod
    def _resolve_aina_project_import(
        existing: AinaProjectRecord,
        candidate: AinaProjectRecord,
    ) -> AinaProjectRecord:
        same_identity = (
            existing.tenant_id == candidate.tenant_id
            and existing.user_id == candidate.user_id
            and existing.manifest.aina.id == candidate.manifest.aina.id
            and existing.manifest.aina.version == candidate.manifest.aina.version
        )
        if same_identity and existing.archive_sha256 == candidate.archive_sha256:
            return existing
        if same_identity:
            raise conflict(
                f"AINA project {candidate.manifest.aina.id!r} version "
                f"{candidate.manifest.aina.version!r} was already imported with different content"
            )
        raise conflict(f"AINA project {candidate.id!r} already exists")

    async def mark_aina_project_validated(
        self,
        project_id: str,
        *,
        archive_sha256: str,
        user_id: str,
        tenant_id: str,
    ) -> AinaProjectRecord:
        async with self._lock:
            project = self._aina_projects.get(project_id)
            if project is None:
                raise not_found("AINA project", project_id)
            require_owner(project, user_id=user_id, tenant_id=tenant_id, label="AINA project")
            updated = validated_aina_project(project, archive_sha256)
            if updated is None:
                return self._copy(project)
            await self._save_record(AINA_PROJECTS_RESOURCE, project_id, updated)
            self._aina_projects[project_id] = updated
            return self._copy(updated)

    async def set_aina_project_deployed(
        self,
        project_id: str,
        *,
        deployed: bool,
        user_id: str,
        tenant_id: str,
    ) -> AinaProjectRecord:
        async with self._lock:
            project = self._aina_projects.get(project_id)
            if project is None:
                raise not_found("AINA project", project_id)
            require_owner(project, user_id=user_id, tenant_id=tenant_id, label="AINA project")
            updated = deployed_aina_project(project, deployed)
            if updated is None:
                return self._copy(project)
            await self._save_record(AINA_PROJECTS_RESOURCE, project_id, updated)
            self._aina_projects[project_id] = updated
            return self._copy(updated)

    async def get_aina_project(
        self,
        project_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> AinaProjectRecord:
        async with self._lock:
            project = self._aina_projects.get(project_id)
            if project is None:
                raise not_found("AINA project", project_id)
            return self._copy(require_owner(project, user_id=user_id, tenant_id=tenant_id, label="AINA project"))

    async def list_aina_projects(self, *, user_id: str, tenant_id: str) -> list[AinaProjectRecord]:
        async with self._lock:
            projects = [
                self._copy(project)
                for project in self._aina_projects.values()
                if project.user_id == user_id and project.tenant_id == tenant_id
            ]
        return sorted(projects, key=lambda project: project.created_at, reverse=True)

    async def remove_aina_project(
        self,
        project_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> None:
        async with self._lock:
            project = self._aina_projects.get(project_id)
            if project is None:
                raise not_found("AINA project", project_id)
            require_owner(project, user_id=user_id, tenant_id=tenant_id, label="AINA project")
            await self._delete_record(AINA_PROJECTS_RESOURCE, project_id)
            del self._aina_projects[project_id]

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
                await self._delete_record(INSTALLATIONS_RESOURCE, installation_record_id(*key))

    async def get_aina_file(self, installation_key: str, path: str) -> AinaFile | None:
        async with self._lock:
            found = self._aina_files.get(file_record_id(installation_key, path))
            return self._copy(found) if found is not None else None

    async def put_aina_file(self, file: AinaFile) -> AinaFile:
        async with self._lock:
            self._aina_files[file.id] = self._copy(file)
            return self._copy(file)

    async def delete_aina_file(self, installation_key: str, path: str) -> AinaFile | None:
        async with self._lock:
            return self._aina_files.pop(file_record_id(installation_key, path), None)

    async def list_aina_files(self, installation_key: str, *, prefix: str, limit: int) -> list[AinaFile]:
        async with self._lock:
            owned = [
                f for f in self._aina_files.values() if f.installation_key == installation_key and f.path.startswith(prefix)
            ]
            return [self._copy(f) for f in sorted(owned, key=lambda f: f.path)[:limit]]

    async def aina_file_usage(self, installation_key: str) -> tuple[int, int]:
        async with self._lock:
            owned = [f for f in self._aina_files.values() if f.installation_key == installation_key]
            return sum(f.size_bytes for f in owned), len(owned)

    async def delete_aina_files(
        self, *, installation_key: str | None = None, aina_id: str | None = None
    ) -> list[AinaFile]:
        async with self._lock:
            doomed = [
                f
                for f in self._aina_files.values()
                if (installation_key is None or f.installation_key == installation_key)
                and (aina_id is None or f.aina_id == aina_id)
            ]
            for f in doomed:
                del self._aina_files[f.id]
            return doomed

    async def put_chat_attachment(self, attachment: Attachment) -> Attachment:
        async with self._lock:
            self._chat_attachments[attachment.id] = self._copy(attachment)
            return self._copy(attachment)

    async def get_chat_attachment(self, file_ref: str) -> Attachment | None:
        async with self._lock:
            found = self._chat_attachments.get(file_ref)
            return self._copy(found) if found is not None else None

    async def list_chat_attachments(self, file_refs: list[str]) -> list[Attachment]:
        async with self._lock:
            return [self._copy(self._chat_attachments[ref]) for ref in file_refs if ref in self._chat_attachments]

    async def bind_chat_attachment(
        self, file_ref: str, *, conversation_id: str, workspace_id: str | None
    ) -> Attachment | None:
        async with self._lock:
            found = self._chat_attachments.get(file_ref)
            if found is not None and found.conversation_id is None:
                found = found.model_copy(update={"conversation_id": conversation_id, "workspace_id": workspace_id})
                self._chat_attachments[file_ref] = found
            return self._copy(found) if found is not None else None

    async def delete_chat_attachment(self, file_ref: str) -> None:
        async with self._lock:
            self._chat_attachments.pop(file_ref, None)

    async def list_unsent_chat_attachments(
        self, *, tenant_id: str, user_id: str, created_before: datetime
    ) -> list[Attachment]:
        async with self._lock:
            return [
                self._copy(item)
                for item in self._chat_attachments.values()
                if (item.tenant_id, item.user_id) == (tenant_id, user_id)
                and item.conversation_id is None
                and item.created_at < created_before
            ]

    async def create_aina_notification(self, notification: AinaNotification) -> AinaNotification:
        async with self._lock:
            self._aina_notifications[notification.id] = self._copy(notification)
            owned = sorted(
                (n for n in self._aina_notifications.values() if (n.tenant_id, n.user_id) == (notification.tenant_id, notification.user_id)),
                key=lambda n: n.created_at,
                reverse=True,
            )
            for stale in owned[KEEP_PER_USER:]:
                del self._aina_notifications[stale.id]
            return self._copy(notification)

    async def list_aina_notifications(self, *, tenant_id: str, user_id: str, limit: int) -> list[AinaNotification]:
        async with self._lock:
            owned = [n for n in self._aina_notifications.values() if (n.tenant_id, n.user_id) == (tenant_id, user_id)]
            return [self._copy(n) for n in sorted(owned, key=lambda n: n.created_at, reverse=True)[:limit]]

    async def count_aina_notifications(
        self, *, tenant_id: str, user_id: str, unread: bool = False, aina_id: str | None = None, since: datetime | None = None
    ) -> int:
        async with self._lock:
            return sum(
                1
                for n in self._aina_notifications.values()
                if (n.tenant_id, n.user_id) == (tenant_id, user_id)
                and (not unread or n.read_at is None)
                and (aina_id is None or n.aina_id == aina_id)
                and (since is None or n.created_at >= since)
            )

    async def mark_aina_notifications_read(
        self, *, tenant_id: str, user_id: str, at: datetime, notification_id: str | None = None
    ) -> int:
        async with self._lock:
            marked = 0
            for key, n in list(self._aina_notifications.items()):
                if (n.tenant_id, n.user_id) != (tenant_id, user_id) or n.read_at is not None:
                    continue
                if notification_id is not None and n.id != notification_id:
                    continue
                self._aina_notifications[key] = n.model_copy(update={"read_at": at})
                marked += 1
            return marked

    async def delete_aina_notifications(self, *, aina_id: str, tenant_id: str | None = None, user_id: str | None = None) -> int:
        async with self._lock:
            doomed = [
                key
                for key, n in self._aina_notifications.items()
                if n.aina_id == aina_id and (tenant_id is None or (n.tenant_id, n.user_id) == (tenant_id, user_id))
            ]
            for key in doomed:
                del self._aina_notifications[key]
            return len(doomed)

    async def create_developer_token(self, token: DeveloperToken) -> DeveloperToken:
        async with self._lock:
            self._developer_tokens[token.id] = self._copy(token)
            return self._copy(token)

    async def list_developer_tokens(self, *, tenant_id: str, user_id: str) -> list[DeveloperToken]:
        async with self._lock:
            owned = [t for t in self._developer_tokens.values() if (t.tenant_id, t.user_id) == (tenant_id, user_id)]
            return [self._copy(t) for t in sorted(owned, key=lambda t: t.created_at, reverse=True)]

    async def find_developer_token(self, token_hash: str) -> DeveloperToken | None:
        async with self._lock:
            found = next((t for t in self._developer_tokens.values() if t.token_hash == token_hash), None)
            return self._copy(found) if found is not None else None

    async def touch_developer_token(self, token: DeveloperToken) -> None:
        async with self._lock:
            if token.id in self._developer_tokens:
                self._developer_tokens[token.id] = self._copy(token)

    async def delete_developer_token(self, token_id: str, *, tenant_id: str, user_id: str) -> bool:
        async with self._lock:
            token = self._developer_tokens.get(token_id)
            if token is None or (token.tenant_id, token.user_id) != (tenant_id, user_id):
                return False
            del self._developer_tokens[token_id]
            return True

    async def get_aina_storage_item(self, installation_key: str, key: str) -> AinaStorageItem | None:
        async with self._lock:
            item = next(
                (
                    item
                    for item in self._aina_storage.values()
                    if item.installation_key == installation_key and item.key == key
                ),
                None,
            )
            return self._copy(item)

    async def put_aina_storage_item(self, item: AinaStorageItem) -> AinaStorageItem:
        async with self._lock:
            owned = [stored for stored in self._aina_storage.values() if stored.installation_key == item.installation_key]
            check_quota(
                used_bytes=sum(stored.size_bytes for stored in owned),
                used_items=len(owned),
                replaced=self._aina_storage.get(item.id),
                item=item,
            )
            self._aina_storage[item.id] = self._copy(item)
        return self._copy(item)

    async def delete_aina_storage_item(self, installation_key: str, key: str) -> bool:
        async with self._lock:
            match = next(
                (
                    item_id
                    for item_id, item in self._aina_storage.items()
                    if item.installation_key == installation_key and item.key == key
                ),
                None,
            )
            return match is not None and self._aina_storage.pop(match, None) is not None

    async def list_aina_storage_items(self, installation_key: str, *, prefix: str, limit: int) -> list[AinaStorageItem]:
        async with self._lock:
            items = sorted(
                (
                    item
                    for item in self._aina_storage.values()
                    if item.installation_key == installation_key and item.key.startswith(prefix)
                ),
                key=lambda item: item.key,
            )
            return [self._copy(item) for item in items[:limit]]

    async def delete_aina_storage(self, *, installation_key: str | None = None, aina_id: str | None = None) -> int:
        """Delete the storage of one installation, or of every installation of one AINA."""
        async with self._lock:
            doomed = [
                item_id
                for item_id, item in self._aina_storage.items()
                if (installation_key is None or item.installation_key == installation_key)
                and (aina_id is None or item.aina_id == aina_id)
            ]
            for item_id in doomed:
                del self._aina_storage[item_id]
            return len(doomed)

    async def create_aina_task(self, task: AinaTask) -> AinaTask:
        async with self._lock:
            if task.id in self._aina_tasks:
                raise conflict(f"Task {task.external_id!r} already exists")
            self._aina_tasks[task.id] = self._copy(task)
        return self._copy(task)

    async def get_aina_task(self, task_id: str) -> AinaTask | None:
        async with self._lock:
            return self._copy(self._aina_tasks.get(task_id))

    async def update_aina_task(self, task_id: str, change: Callable[[AinaTask], AinaTask | None]) -> AinaTask | None:
        """Apply ``change`` atomically; it returns the updated task, or None to leave it as it is."""
        async with self._lock:
            current = self._aina_tasks.get(task_id)
            if current is None:
                return None
            updated = change(self._copy(current))
            if updated is not None:
                self._aina_tasks[task_id] = self._copy(updated)
            return self._copy(updated or current)

    async def list_aina_tasks(self, conversation_id: str) -> list[AinaTask]:
        async with self._lock:
            items = [task for task in self._aina_tasks.values() if task.conversation_id == conversation_id]
            return [self._copy(task) for task in sorted(items, key=lambda task: task.created_at)]

    async def put_installation(self, installation: AinaInstallation) -> AinaInstallation:
        key = (installation.tenant_id, installation.user_id, installation.aina_id)
        async with self._lock:
            self._installations[key] = installation
            await self._save_record(INSTALLATIONS_RESOURCE, installation_record_id(*key), installation)
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
                installation_record_id(tenant_id, user_id, aina_id),
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

    async def add_trace_span(self, trace_id: str, span: TraceSpan) -> TraceSpan:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise not_found("Trace", trace_id)
            if any(item.span_id == span.span_id for item in trace.spans):
                raise conflict(f"Trace span {span.span_id!r} already exists")
            if span.parent_span_id is not None and not any(
                item.span_id == span.parent_span_id for item in trace.spans
            ):
                raise conflict(f"Trace span parent {span.parent_span_id!r} does not exist")
            trace.spans.append(span)
            await self._save_record(TRACES_RESOURCE, trace_id, trace)
            return self._copy(span)

    async def ensure_trace_root_span(self, trace_id: str, span: TraceSpan) -> TraceSpan:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise not_found("Trace", trace_id)
            if trace.root_span_id is not None:
                existing = next((item for item in trace.spans if item.span_id == trace.root_span_id), None)
                if existing is None:
                    raise not_found("Trace span", trace.root_span_id)
                return self._copy(existing)
            if span.parent_span_id is not None:
                raise conflict("Trace root span cannot have a parent")
            trace.root_span_id = span.span_id
            trace.spans.append(span)
            await self._save_record(TRACES_RESOURCE, trace_id, trace)
            return self._copy(span)

    async def finish_trace_span(
        self,
        trace_id: str,
        span_id: str,
        status: str,
        *,
        input_data: Any | None = None,
        output_data: Any | None = None,
        attributes: dict[str, Any] | None = None,
        first_output_at: datetime | None = None,
        error: dict[str, Any] | None = None,
    ) -> TraceSpan:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise not_found("Trace", trace_id)
            span = next((item for item in trace.spans if item.span_id == span_id), None)
            if span is None:
                raise not_found("Trace span", span_id)
            if span.status != "running":
                return self._copy(span)
            completed_at = datetime.now(UTC)
            span.status = status  # type: ignore[assignment]
            span.first_output_at = first_output_at
            span.completed_at = completed_at
            span.duration_ms = max(0.0, (completed_at - span.started_at).total_seconds() * 1000)
            if input_data is not None:
                span.input = input_data
            if output_data is not None:
                span.output = output_data
            if attributes:
                span.attributes.update(attributes)
            span.error = error
            await self._save_record(TRACES_RESOURCE, trace_id, trace)
            return self._copy(span)

    async def finish_trace(self, trace_id: str, status: str) -> TraceRecord:
        async with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                raise not_found("Trace", trace_id)
            trace.status = status  # type: ignore[assignment]
            completed_at = datetime.now(UTC)
            trace.completed_at = completed_at
            if status in {"completed", "failed", "cancelled"}:
                for span in trace.spans:
                    if span.status != "running":
                        continue
                    if span.span_id == trace.root_span_id or status == "failed":
                        span.status = status  # type: ignore[assignment]
                    else:
                        span.status = "cancelled"
                    span.completed_at = completed_at
                    span.duration_ms = max(0.0, (completed_at - span.started_at).total_seconds() * 1000)
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

    async def upsert_llm_call(self, call: LLMCallRecord) -> None:
        async with self._lock:
            self._llm_calls[call.call_id] = call
            await self._save_record(LLM_CALLS_RESOURCE, call.call_id, call)

    async def list_llm_calls(
        self,
        *,
        limit: int = 200,
        offset: int = 0,
        trace_ids: set[str] | None = None,
        context_ids: set[str] | None = None,
    ) -> list[LLMCallRecord]:
        async with self._lock:
            calls = [
                self._copy(item)
                for item in self._llm_calls.values()
                if (trace_ids is None and context_ids is None)
                or (trace_ids is not None and item.trace_id in trace_ids)
                or (context_ids is not None and item.context_id in context_ids)
            ]
        return sorted(calls, key=lambda item: item.created_at, reverse=True)[offset : offset + limit]

    async def count_llm_calls(
        self,
        *,
        trace_ids: set[str] | None = None,
        context_ids: set[str] | None = None,
    ) -> int:
        async with self._lock:
            if trace_ids is None and context_ids is None:
                return len(self._llm_calls)
            return sum(
                1
                for call in self._llm_calls.values()
                if (trace_ids is not None and call.trace_id in trace_ids)
                or (context_ids is not None and call.context_id in context_ids)
            )

    async def create_approval(self, approval: ApprovalRecord) -> ApprovalRecord:
        async with self._lock:
            self._approvals[approval.id] = approval
            await self._save_record(APPROVALS_RESOURCE, approval.id, approval)
        return self._copy(approval)

    async def append_messages(
        self, conversation_id: str, messages: Iterable[Message]
    ) -> Conversation | None:
        """Stable-ID idempotent archive append consumed by ConversationService."""
        async with self._lock:
            conversation = self._conversations.get(conversation_id)
            if conversation is None:
                return None
            existing_ids = {m.id for m in conversation.messages}
            for message in messages:
                if message.id in existing_ids:
                    continue
                conversation.messages.append(message)
                existing_ids.add(message.id)
            conversation.updated_at = datetime.now(UTC)
            await self._save_record(CONVERSATIONS_RESOURCE, conversation_id, conversation)
            return self._copy(conversation)

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

    async def cancel_pending_approvals(self, conversation_id: str) -> list[ApprovalRecord]:
        cancelled: list[ApprovalRecord] = []
        async with self._lock:
            for approval in self._approvals.values():
                if approval.conversation_id == conversation_id and approval.status == "pending":
                    approval.status = "denied"
                    approval.resolved_at = datetime.now(UTC)
                    await self._save_record(APPROVALS_RESOURCE, approval.id, approval)
                    if approval.trace_id in self._traces:
                        cancelled.append(approval)
        return [self._copy(approval) for approval in cancelled]


def require_owner[RecordT: Any](record: RecordT, *, user_id: str, tenant_id: str, label: str) -> RecordT:
    if record.user_id != user_id or record.tenant_id != tenant_id:  # type: ignore[attr-defined]
        raise PlatformError("PERMISSION_DENIED", f"{label} ownership does not match the caller", status_code=403)
    return record


def resubmitted_feedback(existing: FeedbackRecord, feedback: FeedbackRecord) -> FeedbackRecord:
    now = datetime.now(UTC)
    reactivated = not existing.active
    action = "重新提交反馈" if reactivated else "修改反馈"
    return existing.model_copy(
        update={
            "user_name": feedback.user_name,
            "user_email": feedback.user_email,
            "conversation_id": feedback.conversation_id,
            "trace_id": feedback.trace_id,
            "agent_name": feedback.agent_name,
            "agent_version": feedback.agent_version,
            "rating": feedback.rating,
            "reason": feedback.reason,
            "comment": feedback.comment,
            "active": True,
            "case_status": "pending" if feedback.rating == "down" else existing.case_status,
            "created_at": now if reactivated else existing.created_at,
            "updated_at": now,
            "history": [
                *existing.history,
                FeedbackHistoryItem(actor_id=feedback.user_id, actor_name=feedback.user_name, action=action),
            ],
        },
        deep=True,
    )


def cancelled_feedback(item: FeedbackRecord, *, user_id: str, actor_name: str) -> FeedbackRecord:
    return item.model_copy(
        update={
            "active": False,
            "updated_at": datetime.now(UTC),
            "history": [*item.history, FeedbackHistoryItem(actor_id=user_id, actor_name=actor_name, action="取消反馈")],
        },
        deep=True,
    )


def updated_feedback_case(
    item: FeedbackRecord, *, status: str, assignee: str, conclusion: str, actor_id: str, actor_name: str
) -> FeedbackRecord:
    return item.model_copy(
        update={
            "case_status": status,
            "assignee": assignee,
            "conclusion": conclusion,
            "updated_at": datetime.now(UTC),
            "history": [
                *item.history,
                FeedbackHistoryItem(
                    actor_id=actor_id,
                    actor_name=actor_name,
                    action=f"状态更新为 {status}，负责人：{assignee or '未分配'}",
                ),
                *(
                    [FeedbackHistoryItem(actor_id=actor_id, actor_name=actor_name, action=f"处理结论：{conclusion}")]
                    if conclusion
                    else []
                ),
            ],
        },
        deep=True,
    )


MODEL_PROVIDER_LIMIT = 20


def new_model_provider(data: ModelProviderCreate, *, existing_count: int) -> ModelProviderRecord:
    if existing_count >= MODEL_PROVIDER_LIMIT:
        raise conflict("Model provider limit reached")
    return ModelProviderRecord(
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
                context_window_tokens=model.context_window_tokens,
                accepts_images=model.accepts_images,
                max_output_tokens=model.max_output_tokens,
            )
            for model in data.models
        ],
    )


def updated_model_provider(provider: ModelProviderRecord, data: ModelProviderUpdate) -> ModelProviderRecord:
    require_owner(provider, user_id=data.user_id, tenant_id=data.tenant_id, label="Model provider")
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
            context_window_tokens=model.context_window_tokens,
            accepts_images=model.accepts_images,
            max_output_tokens=model.max_output_tokens,
        )
        for model in data.models
    ]
    return provider.model_copy(
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


def providers_with_default_model(
    providers: Iterable[ModelProviderRecord], provider_id: str, model_id: str
) -> list[ModelProviderRecord]:
    """The providers of one actor whose models change when ``model_id`` of ``provider_id`` becomes the default."""
    providers = list(providers)
    provider = next((item for item in providers if item.id == provider_id), None)
    if provider is None:
        raise not_found("Model provider", provider_id)
    selected = next((item for item in provider.models if item.id == model_id), None)
    if selected is None:
        raise not_found("Model", model_id)
    if not selected.enabled:
        raise conflict("A disabled model cannot be selected as default")
    changed: list[ModelProviderRecord] = []
    now = datetime.now(UTC)
    for item in providers:
        models = [
            model.model_copy(update={"is_default": item.id == provider_id and model.id == model_id})
            for model in item.models
        ]
        if models != item.models:
            changed.append(item.model_copy(update={"models": models, "updated_at": now}, deep=True))
    return changed


def default_model_runtime(providers: Iterable[ModelProviderRecord]) -> ModelRuntimeConfig | None:
    """The enabled default model of the first of one actor's providers that has one."""
    for provider in providers:
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
                context_window_tokens=model.context_window_tokens,
                timeout_seconds=provider.timeout_seconds,
                accepts_images=model.accepts_images,
                max_output_tokens=model.max_output_tokens,
            )
    return None


MEMORY_LIMIT = 500


def new_memory(data: MemoryCreate, *, existing_count: int) -> MemoryRecord:
    if existing_count >= MEMORY_LIMIT:
        raise conflict("Memory limit reached; remove or consolidate an existing memory")
    return MemoryRecord(id=f"mem_{uuid4().hex}", **data.model_dump())


def updated_memory(memory: MemoryRecord, data: MemoryUpdate) -> MemoryRecord:
    require_owner(memory, user_id=data.user_id, tenant_id=data.tenant_id, label="Memory")
    changes = data.model_dump(exclude_none=True, exclude={"user_id", "tenant_id"})
    return memory.model_copy(update={**changes, "updated_at": datetime.now(UTC)}, deep=True)


def ranked_memories(query: str, candidates: Iterable[MemoryRecord], *, limit: int) -> list[MemoryRecord]:
    query_terms = _memory_terms(query)
    normalized_query = query.casefold().strip()
    scored: list[tuple[float, MemoryRecord]] = []
    for memory in candidates:
        normalized_content = memory.content.casefold()
        content_terms = _memory_terms(memory.content)
        overlap = len(query_terms & content_terms)
        score = overlap / max(len(query_terms), 1)
        if normalized_query and (normalized_query in normalized_content or normalized_content in normalized_query):
            score += 2.0
        if score > 0:
            scored.append((score, memory))
    scored.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
    return [memory for _score, memory in scored[:limit]]


def memory_stats_of(memories: Iterable[MemoryRecord]) -> MemoryStats:
    counts = {category: 0 for category in ("fact", "preference", "goal", "instruction")}
    total = 0
    for memory in memories:
        counts[memory.category] += 1
        total += 1
    return MemoryStats(total=total, **counts)


def new_workspace(data: WorkspaceCreate) -> Workspace:
    suffix = uuid4().hex
    return Workspace(id=f"workspace_{suffix}", storage_key=f"ws_{suffix}", **data.model_dump())


def updated_workspace(workspace: Workspace, data: WorkspaceUpdate) -> Workspace:
    changes = data.model_dump(exclude_none=True)
    return workspace.model_copy(update={**changes, "updated_at": datetime.now(UTC)}, deep=True)


def document_edit_task_changed() -> PlatformError:
    return conflict("Document edit task changed. Reload it before retrying.")


def next_document_edit_task_version(task: DocumentEditTask, expected_version: int) -> DocumentEditTask:
    return task.model_copy(update={"version": expected_version + 1, "updated_at": datetime.now(UTC)}, deep=True)


def validated_aina_project(project: AinaProjectRecord, archive_sha256: str) -> AinaProjectRecord | None:
    """The project marked validated, or None when it already is (or is deployed)."""
    if project.archive_sha256 != archive_sha256:
        raise conflict("AINA project archive changed while it was being imported")
    if project.status in {"validated", "deployed"}:
        return None
    return project.model_copy(update={"status": "validated", "updated_at": datetime.now(UTC)}, deep=True)


def deployed_aina_project(project: AinaProjectRecord, deployed: bool) -> AinaProjectRecord | None:
    """The project moved to deployed or back to validated, or None when it already has that status."""
    target_status = "deployed" if deployed else "validated"
    if project.status == target_status:
        return None
    if project.status == "importing":
        raise conflict("AINA project import has not completed")
    now = datetime.now(UTC)
    return project.model_copy(
        update={"status": target_status, "deployed_at": now if deployed else None, "updated_at": now}, deep=True
    )


def started_scheduled_aina_run(
    task: ScheduledAinaTask, *, node_id: str, started_at: datetime, scheduled_for: datetime | None = None
) -> ScheduledAinaTask | None:
    if scheduled_for is not None and (not task.enabled or task.next_run_at != scheduled_for):
        return None
    return task.model_copy(update={"last_status": "running", "last_node_id": node_id, "updated_at": started_at})


def finished_scheduled_aina_run(
    task: ScheduledAinaTask,
    *,
    status: str,
    error: str | None,
    result: dict[str, Any] | None,
    finished_at: datetime,
    advance_schedule: bool,
) -> ScheduledAinaTask:
    """Record a finished run on the task as it is now, so edits made while it ran are kept."""
    return task.model_copy(
        update={
            "next_run_at": next_scheduled_run(task, after=finished_at) if advance_schedule else task.next_run_at,
            "last_run_at": finished_at,
            "last_status": status,
            "last_error": error,
            "last_result": result,
            "updated_at": finished_at,
        },
        deep=True,
    )


def new_scheduled_aina_task(data: ScheduledAinaTaskCreate) -> ScheduledAinaTask:
    now = datetime.now(UTC)
    values = data.model_dump()
    if data.prompt is not None:
        values["input"] = {"message": data.prompt}
    task = ScheduledAinaTask(**values, created_at=now, updated_at=now)
    return task.model_copy(update={"next_run_at": next_scheduled_run(task, after=now)})


def updated_scheduled_aina_task(task: ScheduledAinaTask, data: ScheduledAinaTaskUpdate) -> ScheduledAinaTask:
    changes = data.model_dump(exclude_none=True)
    if "aina_id" in data.model_fields_set:
        changes["aina_id"] = data.aina_id or ""
    if "prompt" in data.model_fields_set:
        changes["prompt"] = data.prompt
    if data.prompt is not None:
        changes["input"] = {"message": data.prompt}
    elif data.input is not None:
        changes["prompt"] = None
    now = datetime.now(UTC)
    changes["updated_at"] = now
    updated = ScheduledAinaTask.model_validate({**task.model_dump(), **changes})
    schedule_fields = {"schedule_type", "interval_seconds", "cron_expression", "timezone"}
    reenabled = data.enabled is True and not task.enabled
    if schedule_fields.intersection(changes) or reenabled:
        updated = updated.model_copy(update={"next_run_at": next_scheduled_run(updated, after=now)})
    return updated


def normalized_email(email: str) -> str:
    """Account emails compare case-insensitively; this is the form stored and indexed for uniqueness."""
    return email.strip().lower()


def email_registered() -> PlatformError:
    return PlatformError(
        "CONFLICT",
        "A user with this email already exists",
        status_code=409,
        source="auth",
        user_message="该邮箱已注册。",
    )


def github_already_linked() -> PlatformError:
    return PlatformError(
        "CONFLICT",
        "This GitHub account is already linked",
        status_code=409,
        source="auth",
        user_message="该 GitHub 账号已绑定其他用户。",
    )


def github_email_registered() -> PlatformError:
    return PlatformError(
        "CONFLICT",
        "The verified GitHub email already belongs to a local account",
        status_code=409,
        source="auth",
        user_message="该邮箱已有账户，请先使用邮箱登录。",
    )


def huawei_already_linked() -> PlatformError:
    return PlatformError(
        "CONFLICT",
        "This Huawei account is already linked",
        status_code=409,
        source="auth",
        user_message="该华为账号已绑定其他用户。",
    )


def oidc_already_linked(provider: str) -> PlatformError:
    return conflict(f"This {provider} account is already linked")


def linked_email_registered(provider: str) -> PlatformError:
    """A first sign-in through ``provider`` whose email already has an account."""
    return PlatformError(
        "CONFLICT",
        f"The {provider} email already belongs to an existing account",
        status_code=409,
        source="auth",
        user_message="该邮箱已有账户，请使用原登录方式。",
    )


def new_huawei_user(*, huawei_id: str, email: str, name: str) -> UserRecord:
    return UserRecord(id=f"user_{uuid4().hex}", email=email, name=name, huawei_id=huawei_id)


def relinked_huawei_user(user: UserRecord, *, name: str) -> UserRecord:
    return user.model_copy(update={"name": name, "updated_at": datetime.now(UTC)}, deep=True)


def new_oidc_user(
    *, provider: OidcProvider, identity: str, email: str, name: str | None, avatar_url: str | None
) -> UserRecord:
    return UserRecord(
        id=f"user_{uuid4().hex}",
        email=email,
        name=name or f"{provider.capitalize()} user",
        avatar_url=avatar_url,
        oidc_identities={provider: identity},
    )


def relinked_oidc_user(user: UserRecord, *, name: str | None, avatar_url: str | None) -> UserRecord:
    return user.model_copy(
        update={"name": name or user.name, "avatar_url": avatar_url or user.avatar_url, "updated_at": datetime.now(UTC)},
        deep=True,
    )


def new_github_user(
    *, github_id: str, github_login: str, email: str, name: str, avatar_url: str | None
) -> UserRecord:
    return UserRecord(
        id=f"user_{uuid4().hex}",
        email=email,
        name=name,
        github_id=github_id,
        github_login=github_login,
        avatar_url=avatar_url,
    )


def relinked_github_user(user: UserRecord, *, github_login: str, name: str, avatar_url: str | None) -> UserRecord:
    return user.model_copy(
        update={
            "github_login": github_login,
            "name": name or user.name,
            "avatar_url": avatar_url or user.avatar_url,
            "updated_at": datetime.now(UTC),
        },
        deep=True,
    )


def new_conversation(data: ConversationCreate) -> Conversation:
    return Conversation(id=f"conv_{uuid4().hex}", **data.model_dump())


# Conversation state transitions shared by every repository implementation. Each takes the current record and
# returns the updated copy; callers own locking, existence checks and persistence.


def updated_conversation(conversation: Conversation, data: ConversationUpdate) -> Conversation:
    changes = data.model_dump(exclude_none=True)
    return conversation.model_copy(update={**changes, "updated_at": datetime.now(UTC)}, deep=True)


def conversation_with_status(conversation: Conversation, status: str) -> Conversation:
    return conversation.model_copy(update={"status": status, "updated_at": datetime.now(UTC)}, deep=True)


def started_run(conversation: Conversation, trace_id: str) -> Conversation:
    if conversation.run_status == "running":
        raise conflict("This conversation already has a running request")
    return conversation.model_copy(
        update={
            "run_status": "running",
            "active_trace_id": trace_id,
            "run_error": None,
            "run_started_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
        deep=True,
    )


def finished_run(conversation: Conversation, *, status: str, error: str | None) -> Conversation:
    return conversation.model_copy(
        update={
            "run_status": status,
            "active_trace_id": None,
            "run_error": error,
            "run_started_at": None,
            "updated_at": datetime.now(UTC),
        },
        deep=True,
    )


def with_bound_aina(
    conversation: Conversation, aina_id: str, *, make_primary: bool, mark_used: bool
) -> Conversation:
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
    return conversation.model_copy(update=changes, deep=True)


def message_from_provider(raw: dict[str, Any], *, trace_id: str) -> Message:
    return Message(
        id=f"msg_{uuid4().hex}",
        role=raw["role"],
        content=raw.get("content") or "",
        tool_calls=raw.get("tool_calls"),
        tool_call_id=raw.get("tool_call_id"),
        name=raw.get("name"),
        widgets=raw.get("widgets") or [],
        content_type="tool" if raw["role"] == "tool" else "widget" if raw.get("widgets") else "text",
        trace_id=trace_id,
    )


def installation_record_id(tenant_id: str, user_id: str, aina_id: str) -> str:
    raw = json.dumps([tenant_id, user_id, aina_id], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _memory_terms(value: str) -> set[str]:
    normalized = value.casefold()
    words = set(re.findall(r"[a-z0-9_]+", normalized))
    cjk = re.findall(r"[\u3400-\u9fff]", normalized)
    words.update(cjk)
    words.update("".join(cjk[index : index + 2]) for index in range(len(cjk) - 1))
    return words
