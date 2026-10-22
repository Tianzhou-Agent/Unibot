from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from tianzhou_agent_platform.auth.models import DeveloperToken, OidcProvider, UserRecord
from tianzhou_agent_platform.aina.project import AinaProjectRecord
from tianzhou_agent_platform.aina.memory.models import MemoryCreate, MemoryRecord, MemoryUpdate
from tianzhou_agent_platform.aina.document.task_models import DocumentEditTask
from tianzhou_agent_platform.aina.package.files import AinaFile
from tianzhou_agent_platform.conversations.attachments import Attachment
from tianzhou_agent_platform.aina.package.notifications import AinaNotification
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation, AinaRecord
from tianzhou_agent_platform.aina.skill.models import SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolRecord
from tianzhou_agent_platform.conversations.models import (
    CanvasTabs,
    Conversation,
    ConversationCreate,
    ConversationUpdate,
    Message,
)
from tianzhou_agent_platform.conversations.schemas import ApprovalRecord
from tianzhou_agent_platform.core.errors import PlatformError, conflict, not_found
from tianzhou_agent_platform.core.feedback import FeedbackRecord
from tianzhou_agent_platform.core.workspace import Workspace, WorkspaceCreate, WorkspaceUpdate
from tianzhou_agent_platform.model_providers.models import (
    ModelProviderCreate,
    ModelProviderRecord,
    ModelProviderUpdate,
    ModelRuntimeConfig,
)
from tianzhou_agent_platform.observability.models import LLMCallRecord, TraceRecord
from tianzhou_agent_platform.aina.scheduler import (
    ScheduledAinaExecution,
    ScheduledAinaTask,
    ScheduledAinaTaskCreate,
    ScheduledAinaTaskUpdate,
)
from tianzhou_agent_platform.store.memory_repository import (
    LLM_CALLS_RESOURCE,
    TRACES_RESOURCE,
    InMemoryRepository,
    INTERRUPTED_RUN_ERROR,
    RUN_RECOVERY_CLAIM,
    conversation_with_status,
    deployed_aina_project,
    finished_run,
    message_from_provider,
    new_scheduled_aina_task,
    new_conversation,
    new_workspace,
    require_owner,
    started_run,
    updated_conversation,
    validated_aina_project,
    with_bound_aina,
    with_canvas_tabs,
)
from tianzhou_agent_platform.store.conversation_store import ConversationStore
from tianzhou_agent_platform.store.user_store import UserStore
from tianzhou_agent_platform.store.schedule_store import ScheduleStore
from tianzhou_agent_platform.store.sandbox_store import SandboxStore
from tianzhou_agent_platform.store.workspace_store import WorkspaceStore
from tianzhou_agent_platform.store.memory_store import MemoryStore
from tianzhou_agent_platform.store.model_provider_store import ModelProviderStore
from tianzhou_agent_platform.store.feedback_store import FeedbackStore
from tianzhou_agent_platform.store.document_task_store import DocumentTaskStore
from tianzhou_agent_platform.aina.package.storage import AinaStorageItem
from tianzhou_agent_platform.aina.package.tasks import AinaTask
from tianzhou_agent_platform.store.aina_storage_store import AinaStorageStore
from tianzhou_agent_platform.store.developer_token_store import DeveloperTokenStore
from tianzhou_agent_platform.store.aina_notification_store import AinaNotificationStore
from tianzhou_agent_platform.store.aina_file_store import AinaFileStore
from tianzhou_agent_platform.store.chat_attachment_store import ChatAttachmentStore
from tianzhou_agent_platform.store.aina_task_store import AinaTaskStore
from tianzhou_agent_platform.store.aina_project_store import AinaProjectStore
from tianzhou_agent_platform.store.registry_store import RegistryStore
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.models import StoreQuery
from tianzhou_agent_platform.store.repository_schema import (
    model_from_row,
    repository_metadata as repository_metadata,
    repository_tables as repository_tables,
    row_values,
)
from tianzhou_agent_platform.sandbox.models import SandboxExecution, SandboxRecord

# A live run renews its lease every heartbeat (and cancels itself when it cannot), so the lease only has to
# outlast a few missed heartbeats. When the process running a turn dies, the conversation is released this soon.
CONVERSATION_RUN_HEARTBEAT_SECONDS = 30
CONVERSATION_RUN_TTL_SECONDS = 4 * CONVERSATION_RUN_HEARTBEAT_SECONDS


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
        self._schedule_store = ScheduleStore(stores.mysql)
        self._sandbox_store = SandboxStore(stores.mysql)
        self._workspace_store = WorkspaceStore(stores.mysql)
        self._memory_store = MemoryStore(stores.mysql)
        self._model_provider_store = ModelProviderStore(stores.mysql)
        self._feedback_store = FeedbackStore(stores.mysql)
        self._document_task_store = DocumentTaskStore(stores.mysql)
        self._aina_project_store = AinaProjectStore(stores.mysql)
        self._registry_store = RegistryStore(stores.mysql)
        self._aina_storage_store = AinaStorageStore(stores.mysql)
        self._developer_token_store = DeveloperTokenStore(stores.mysql)
        self._aina_notification_store = AinaNotificationStore(stores.mysql)
        self._aina_file_store = AinaFileStore(stores.mysql)
        self._chat_attachment_store = ChatAttachmentStore(stores.mysql)
        self._aina_task_store = AinaTaskStore(stores.mysql)

    async def initialize(self) -> None:
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
            # The normal OBS path does not load historical Trace/LLMCall rows
            # at startup. The rollback switch restores the complete legacy
            # behavior, including restart recovery.
            self._traces = {item.trace_id: item for item in traces}
            self._llm_calls = {item.call_id: item for item in llm_calls}
            self._legacy_traces_loaded = self.persist_observability
            self._legacy_llm_calls_loaded = self.persist_observability

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

    async def upsert_huawei_user(self, *, huawei_id: str, email: str, name: str) -> UserRecord:
        return await self._user_store.upsert_huawei(huawei_id=huawei_id, email=email, name=name)

    async def upsert_oidc_user(
        self, *, provider: OidcProvider, identity: str, email: str, name: str | None, avatar_url: str | None
    ) -> UserRecord:
        return await self._user_store.upsert_oidc(
            provider=provider, identity=identity, email=email, name=name, avatar_url=avatar_url
        )

    async def create_workspace(self, data: WorkspaceCreate) -> Workspace:
        workspace = new_workspace(data)
        await self._workspace_store.create(workspace)
        return workspace

    async def get_workspace(self, workspace_id: str) -> Workspace:
        workspace = await self._workspace_store.get(workspace_id)
        if workspace is None:
            raise not_found("Workspace", workspace_id)
        return workspace

    async def list_workspaces(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[Workspace]:
        return await self._workspace_store.list_workspaces(user_id=user_id, tenant_id=tenant_id)

    async def update_workspace(self, workspace_id: str, data: WorkspaceUpdate) -> Workspace:
        workspace = await self._workspace_store.update(workspace_id, data)
        if workspace is None:
            raise not_found("Workspace", workspace_id)
        return workspace

    async def create_memory(self, data: MemoryCreate) -> MemoryRecord:
        return await self._memory_store.create(data)

    async def get_memory(self, memory_id: str, *, user_id: str, tenant_id: str) -> MemoryRecord:
        memory = await self._memory_store.get(memory_id)
        if memory is None:
            raise not_found("Memory", memory_id)
        return require_owner(memory, user_id=user_id, tenant_id=tenant_id, label="Memory")

    async def list_memories(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        query: str | None = None,
        category: str | None = None,
    ) -> list[MemoryRecord]:
        return await self._memory_store.list_memories(
            user_id=user_id, tenant_id=tenant_id, query=query, category=category
        )

    async def update_memory(self, memory_id: str, data: MemoryUpdate) -> MemoryRecord:
        memory = await self._memory_store.update(memory_id, data)
        if memory is None:
            raise not_found("Memory", memory_id)
        return memory

    async def remove_memory(self, memory_id: str, *, user_id: str, tenant_id: str) -> None:
        await self.get_memory(memory_id, user_id=user_id, tenant_id=tenant_id)
        await self._memory_store.remove(memory_id)

    async def create_model_provider(self, data: ModelProviderCreate) -> ModelProviderRecord:
        return await self._model_provider_store.create(data)

    async def get_model_provider(
        self,
        provider_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> ModelProviderRecord:
        provider = await self._model_provider_store.get(provider_id)
        if provider is None:
            raise not_found("Model provider", provider_id)
        return require_owner(provider, user_id=user_id, tenant_id=tenant_id, label="Model provider")

    async def list_model_providers(self, *, user_id: str, tenant_id: str) -> list[ModelProviderRecord]:
        return await self._model_provider_store.list_for_actor(user_id=user_id, tenant_id=tenant_id)

    async def update_model_provider(self, provider_id: str, data: ModelProviderUpdate) -> ModelProviderRecord:
        provider = await self._model_provider_store.update(provider_id, data)
        if provider is None:
            raise not_found("Model provider", provider_id)
        return provider

    async def remove_model_provider(self, provider_id: str, *, user_id: str, tenant_id: str) -> None:
        await self.get_model_provider(provider_id, user_id=user_id, tenant_id=tenant_id)
        await self._model_provider_store.remove(provider_id)

    async def set_default_model(
        self,
        provider_id: str,
        model_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> ModelProviderRecord:
        await self.get_model_provider(provider_id, user_id=user_id, tenant_id=tenant_id)
        return await self._model_provider_store.set_default_model(
            provider_id, model_id, user_id=user_id, tenant_id=tenant_id
        )

    async def get_default_model_runtime(self, *, user_id: str, tenant_id: str) -> ModelRuntimeConfig | None:
        return await self._model_provider_store.default_runtime(user_id=user_id, tenant_id=tenant_id)

    async def upsert_feedback(self, feedback: FeedbackRecord) -> FeedbackRecord:
        return await self._feedback_store.upsert(feedback)

    async def get_feedback_for_message(
        self,
        message_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> FeedbackRecord | None:
        return await self._feedback_store.active_for_message(message_id, user_id=user_id, tenant_id=tenant_id)

    async def get_feedback(self, feedback_id: str) -> FeedbackRecord:
        feedback = await self._feedback_store.get_active(feedback_id)
        if feedback is None:
            raise not_found("Feedback", feedback_id)
        return feedback

    async def list_feedbacks(
        self,
        *,
        from_at: datetime | None = None,
        to_at: datetime | None = None,
        rating: str | None = None,
        user_query: str | None = None,
    ) -> list[FeedbackRecord]:
        return await self._feedback_store.list_active(
            from_at=from_at, to_at=to_at, rating=rating, user_query=user_query
        )

    async def cancel_feedback(
        self,
        message_id: str,
        *,
        user_id: str,
        tenant_id: str,
        actor_name: str,
    ) -> None:
        await self._feedback_store.cancel(message_id, user_id=user_id, tenant_id=tenant_id, actor_name=actor_name)

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
        return await self._feedback_store.update_case(
            feedback_id,
            status=status,
            assignee=assignee,
            conclusion=conclusion,
            actor_id=actor_id,
            actor_name=actor_name,
        )

    async def create_aina_project(self, project: AinaProjectRecord) -> AinaProjectRecord:
        return await self._aina_project_store.create_project(project)

    async def mark_aina_project_validated(
        self,
        project_id: str,
        *,
        archive_sha256: str,
        user_id: str,
        tenant_id: str,
    ) -> AinaProjectRecord:
        return await self._change_aina_project(
            project_id,
            user_id=user_id,
            tenant_id=tenant_id,
            change=lambda project: validated_aina_project(project, archive_sha256),
        )

    async def set_aina_project_deployed(
        self,
        project_id: str,
        *,
        deployed: bool,
        user_id: str,
        tenant_id: str,
    ) -> AinaProjectRecord:
        return await self._change_aina_project(
            project_id,
            user_id=user_id,
            tenant_id=tenant_id,
            change=lambda project: deployed_aina_project(project, deployed),
        )

    async def _change_aina_project(
        self,
        project_id: str,
        *,
        user_id: str,
        tenant_id: str,
        change: Callable[[AinaProjectRecord], AinaProjectRecord | None],
    ) -> AinaProjectRecord:
        project = await self._aina_project_store.update_project(
            project_id,
            lambda current: change(require_owner(current, user_id=user_id, tenant_id=tenant_id, label="AINA project")),
        )
        if project is None:
            raise not_found("AINA project", project_id)
        return project

    async def get_aina_project(
        self,
        project_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> AinaProjectRecord:
        project = await self._aina_project_store.get_project(project_id)
        if project is None:
            raise not_found("AINA project", project_id)
        return require_owner(project, user_id=user_id, tenant_id=tenant_id, label="AINA project")

    async def list_aina_projects(self, *, user_id: str, tenant_id: str) -> list[AinaProjectRecord]:
        return await self._aina_project_store.list_projects(user_id=user_id, tenant_id=tenant_id)

    async def remove_aina_project(
        self,
        project_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> None:
        await self.get_aina_project(project_id, user_id=user_id, tenant_id=tenant_id)
        await self._aina_project_store.remove_project(project_id)

    async def register_tool(self, tool: ToolRecord) -> ToolRecord:
        await self._registry_store.register_tool(tool)
        return self._copy(tool)

    async def get_tool(self, tool_id: str) -> ToolRecord:
        tool = await self._registry_store.get_tool(tool_id)
        if tool is None:
            raise not_found("Tool", tool_id)
        return tool

    async def list_tools(self) -> list[ToolRecord]:
        return await self._registry_store.list_tools()

    async def remove_tool(self, tool_id: str) -> None:
        if not await self._registry_store.remove_tool(tool_id):
            raise not_found("Tool", tool_id)

    async def register_skill(self, skill: SkillRecord) -> SkillRecord:
        await self._registry_store.register_skill(skill)
        return self._copy(skill)

    async def get_skill(self, skill_id: str) -> SkillRecord:
        skill = await self._registry_store.get_skill(skill_id)
        if skill is None:
            raise not_found("Skill", skill_id)
        return skill

    async def list_skills(self) -> list[SkillRecord]:
        return await self._registry_store.list_skills()

    async def remove_skill(self, skill_id: str) -> None:
        if not await self._registry_store.remove_skill(skill_id):
            raise not_found("Skill", skill_id)

    async def register_aina(self, aina: AinaRecord) -> AinaRecord:
        await self._registry_store.register_aina(aina)
        return self._copy(aina)

    async def upsert_aina(self, aina: AinaRecord) -> AinaRecord:
        await self._registry_store.put_aina(aina)
        return self._copy(aina)

    async def get_aina(self, aina_id: str) -> AinaRecord:
        aina = await self._registry_store.get_aina(aina_id)
        if aina is None:
            raise not_found("AINA", aina_id)
        return aina

    async def list_ainas(self) -> list[AinaRecord]:
        return await self._registry_store.list_ainas()

    async def remove_aina(self, aina_id: str) -> None:
        if not await self._registry_store.remove_aina(aina_id):
            raise not_found("AINA", aina_id)

    async def get_aina_file(self, installation_key: str, path: str) -> AinaFile | None:
        return await self._aina_file_store.get(installation_key, path)

    async def put_aina_file(self, file: AinaFile) -> AinaFile:
        await self._aina_file_store.put(file)
        return self._copy(file)

    async def delete_aina_file(self, installation_key: str, path: str) -> AinaFile | None:
        return await self._aina_file_store.delete(installation_key, path)

    async def list_aina_files(self, installation_key: str, *, prefix: str, limit: int) -> list[AinaFile]:
        return await self._aina_file_store.list_files(installation_key, prefix=prefix, limit=limit)

    async def aina_file_usage(self, installation_key: str) -> tuple[int, int]:
        return await self._aina_file_store.usage(installation_key)

    async def delete_aina_files(
        self, *, installation_key: str | None = None, aina_id: str | None = None
    ) -> list[AinaFile]:
        return await self._aina_file_store.delete_all(installation_key=installation_key, aina_id=aina_id)

    async def put_chat_attachment(self, attachment: Attachment) -> Attachment:
        await self._chat_attachment_store.put(attachment)
        return self._copy(attachment)

    async def get_chat_attachment(self, file_ref: str) -> Attachment | None:
        return await self._chat_attachment_store.get(file_ref)

    async def list_chat_attachments(self, file_refs: list[str]) -> list[Attachment]:
        return await self._chat_attachment_store.list_refs(file_refs)

    async def bind_chat_attachment(
        self, file_ref: str, *, conversation_id: str, workspace_id: str | None
    ) -> Attachment | None:
        return await self._chat_attachment_store.bind(
            file_ref, conversation_id=conversation_id, workspace_id=workspace_id
        )

    async def delete_chat_attachment(self, file_ref: str) -> None:
        await self._chat_attachment_store.delete(file_ref)

    async def list_unsent_chat_attachments(
        self, *, tenant_id: str, user_id: str, created_before: datetime
    ) -> list[Attachment]:
        return await self._chat_attachment_store.list_unsent(
            tenant_id=tenant_id, user_id=user_id, created_before=created_before
        )

    async def create_aina_notification(self, notification: AinaNotification) -> AinaNotification:
        await self._aina_notification_store.create(notification)
        return self._copy(notification)

    async def list_aina_notifications(self, *, tenant_id: str, user_id: str, limit: int) -> list[AinaNotification]:
        return await self._aina_notification_store.list(tenant_id=tenant_id, user_id=user_id, limit=limit)

    async def count_aina_notifications(
        self, *, tenant_id: str, user_id: str, unread: bool = False, aina_id: str | None = None, since: datetime | None = None
    ) -> int:
        return await self._aina_notification_store.count(
            tenant_id=tenant_id, user_id=user_id, unread=unread, aina_id=aina_id, since=since
        )

    async def mark_aina_notifications_read(
        self, *, tenant_id: str, user_id: str, at: datetime, notification_id: str | None = None
    ) -> int:
        return await self._aina_notification_store.mark_read(
            tenant_id=tenant_id, user_id=user_id, at=at, notification_id=notification_id
        )

    async def delete_aina_notifications(self, *, aina_id: str, tenant_id: str | None = None, user_id: str | None = None) -> int:
        return await self._aina_notification_store.delete(tenant_id=tenant_id, user_id=user_id, aina_id=aina_id)

    async def create_developer_token(self, token: DeveloperToken) -> DeveloperToken:
        await self._developer_token_store.create(token)
        return self._copy(token)

    async def list_developer_tokens(self, *, tenant_id: str, user_id: str) -> list[DeveloperToken]:
        return await self._developer_token_store.list(tenant_id=tenant_id, user_id=user_id)

    async def find_developer_token(self, token_hash: str) -> DeveloperToken | None:
        return await self._developer_token_store.find(token_hash)

    async def touch_developer_token(self, token: DeveloperToken) -> None:
        await self._developer_token_store.touch(token)

    async def delete_developer_token(self, token_id: str, *, tenant_id: str, user_id: str) -> bool:
        return await self._developer_token_store.delete(token_id, tenant_id=tenant_id, user_id=user_id)

    async def get_aina_storage_item(self, installation_key: str, key: str) -> AinaStorageItem | None:
        return await self._aina_storage_store.get(installation_key, key)

    async def put_aina_storage_item(self, item: AinaStorageItem) -> AinaStorageItem:
        await self._aina_storage_store.put(item)
        return self._copy(item)

    async def delete_aina_storage_item(self, installation_key: str, key: str) -> bool:
        return await self._aina_storage_store.delete(installation_key, key)

    async def list_aina_storage_items(self, installation_key: str, *, prefix: str, limit: int) -> list[AinaStorageItem]:
        return await self._aina_storage_store.list(installation_key, prefix=prefix, limit=limit)

    async def delete_aina_storage(self, *, installation_key: str | None = None, aina_id: str | None = None) -> int:
        return await self._aina_storage_store.delete_all(installation_key=installation_key, aina_id=aina_id)

    async def create_aina_task(self, task: AinaTask) -> AinaTask:
        await self._aina_task_store.create(task)
        return self._copy(task)

    async def get_aina_task(self, task_id: str) -> AinaTask | None:
        return await self._aina_task_store.get(task_id)

    async def update_aina_task(self, task_id: str, change: Callable[[AinaTask], AinaTask | None]) -> AinaTask | None:
        return await self._aina_task_store.update(task_id, change)

    async def list_aina_tasks(self, conversation_id: str) -> list[AinaTask]:
        return await self._aina_task_store.list(conversation_id)

    async def put_installation(self, installation: AinaInstallation) -> AinaInstallation:
        await self._aina_project_store.put_installation(installation)
        return self._copy(installation)

    async def get_installation(self, *, tenant_id: str, user_id: str, aina_id: str) -> AinaInstallation:
        installation = await self._aina_project_store.get_installation(
            tenant_id=tenant_id, user_id=user_id, aina_id=aina_id
        )
        if installation is None:
            raise not_found("AINA installation", aina_id)
        return installation

    async def list_installations(
        self,
        *,
        tenant_id: str | None = None,
        user_id: str | None = None,
    ) -> list[AinaInstallation]:
        return await self._aina_project_store.list_installations(tenant_id=tenant_id, user_id=user_id)

    async def remove_installation(self, *, tenant_id: str, user_id: str, aina_id: str) -> None:
        if not await self._aina_project_store.remove_installation(
            tenant_id=tenant_id, user_id=user_id, aina_id=aina_id
        ):
            raise not_found("AINA installation", aina_id)

    async def put_sandbox(self, sandbox: SandboxRecord) -> SandboxRecord:
        await self._sandbox_store.put(sandbox)
        return self._copy(sandbox)

    async def get_sandbox_for_actor(
        self,
        *,
        user_id: str,
        tenant_id: str,
        workspace_id: str | None = None,
    ) -> SandboxRecord:
        sandbox = await self._sandbox_store.get_for_actor(
            user_id=user_id, tenant_id=tenant_id, workspace_id=workspace_id
        )
        if sandbox is None:
            raise not_found("Sandbox", f"{tenant_id}/{user_id}/{workspace_id or 'standalone'}")
        return sandbox

    async def put_sandbox_execution(self, execution: SandboxExecution) -> SandboxExecution:
        await self._sandbox_store.put_execution(execution)
        return self._copy(execution)

    async def list_sandbox_executions(
        self,
        *,
        user_id: str,
        tenant_id: str,
        workspace_id: str | None = None,
        limit: int = 50,
    ) -> list[SandboxExecution]:
        return await self._sandbox_store.list_executions(
            user_id=user_id, tenant_id=tenant_id, workspace_id=workspace_id, limit=limit
        )

    async def remove_sandbox(self, sandbox_id: str) -> None:
        if not await self._sandbox_store.remove(sandbox_id):
            raise not_found("Sandbox", sandbox_id)

    async def create_document_edit_task(self, task: DocumentEditTask) -> DocumentEditTask:
        await self._document_task_store.create(task)
        return self._copy(task)

    async def get_document_edit_task(
        self,
        task_id: str,
        *,
        user_id: str,
        tenant_id: str,
    ) -> DocumentEditTask:
        task = await self._document_task_store.get(task_id)
        if task is None:
            raise not_found("Document edit task", task_id)
        return require_owner(task, user_id=user_id, tenant_id=tenant_id, label="Document edit task")

    async def list_document_edit_tasks(
        self,
        *,
        user_id: str | None = None,
        tenant_id: str | None = None,
        document_name: str | None = None,
        workspace_id: str | None = None,
    ) -> list[DocumentEditTask]:
        return await self._document_task_store.list_tasks(
            user_id=user_id, tenant_id=tenant_id, document_name=document_name, workspace_id=workspace_id
        )

    async def put_document_edit_task(
        self,
        task: DocumentEditTask,
        *,
        expected_version: int,
    ) -> DocumentEditTask:
        return await self._document_task_store.put(task, expected_version=expected_version)

    async def create_scheduled_aina_task(self, data: ScheduledAinaTaskCreate) -> ScheduledAinaTask:
        task = new_scheduled_aina_task(data)
        await self._schedule_store.create_task(task)
        return task

    async def put_scheduled_aina_task(self, task: ScheduledAinaTask) -> ScheduledAinaTask:
        await self._schedule_store.put_task(task)
        return self._copy(task)

    async def list_scheduled_aina_tasks(
        self, *, user_id: str | None = None, tenant_id: str | None = None
    ) -> list[ScheduledAinaTask]:
        return await self._schedule_store.list_tasks(user_id=user_id, tenant_id=tenant_id)

    async def list_due_scheduled_aina_tasks(self, now: datetime) -> list[ScheduledAinaTask]:
        return await self._schedule_store.list_due_tasks(now)

    async def get_scheduled_aina_task(self, task_id: str) -> ScheduledAinaTask:
        task = await self._schedule_store.get_task(task_id)
        if task is None:
            raise not_found("Scheduled AINA task", task_id)
        return task

    async def update_scheduled_aina_task(self, task_id: str, data: ScheduledAinaTaskUpdate) -> ScheduledAinaTask:
        return await self._schedule_store.update_task(task_id, data)

    async def remove_scheduled_aina_task(self, task_id: str) -> None:
        if not await self._schedule_store.remove_task(task_id):
            raise not_found("Scheduled AINA task", task_id)

    async def _record_scheduled_aina_run(
        self, task_id: str, change: Callable[[ScheduledAinaTask], ScheduledAinaTask | None]
    ) -> ScheduledAinaTask | None:
        applied = False

        def apply(task: ScheduledAinaTask) -> ScheduledAinaTask | None:
            nonlocal applied
            updated = change(task)
            applied = updated is not None
            return updated

        updated = await self._schedule_store.change_task(task_id, apply)
        return updated if applied else None

    async def put_scheduled_aina_execution(self, execution: ScheduledAinaExecution) -> ScheduledAinaExecution:
        await self._schedule_store.put_execution(execution)
        return self._copy(execution)

    async def list_scheduled_aina_executions(self, task_id: str, *, limit: int = 50) -> list[ScheduledAinaExecution]:
        return await self._schedule_store.list_executions(task_id, limit=limit)

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

    async def list_interrupted_conversation_runs(self, *, since: datetime) -> list[Conversation]:
        """Conversations whose turn ended with the process running it, since ``since``: still marked running while
        no process holds the run lease, or already reconciled as interrupted."""
        candidates = await self._conversation_store.list_with_run_status(
            running=True, failed_with=INTERRUPTED_RUN_ERROR, since=since
        )
        return [
            conversation
            for conversation in candidates
            if conversation.run_status == "failed"
            or await self.stores.redis.get("conversation-run", conversation.id) is None
        ]

    async def claim_conversation_run_recovery(self, conversation_id: str) -> bool:
        """Whether this node may resume the conversation's interrupted turn: once an hour, by one node."""
        claimed = await self.stores.redis.set_if_absent(
            "conversation-run-recovery",
            conversation_id,
            {"claimed_at": datetime.now(UTC).isoformat()},
            ttl_seconds=int(RUN_RECOVERY_CLAIM.total_seconds()),
        )
        return claimed.written

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

    async def set_conversation_canvas_tabs(self, conversation_id: str, tabs: CanvasTabs) -> Conversation:
        return await self._conversation_store.mutate(
            conversation_id, lambda conversation: with_canvas_tabs(conversation, tabs)
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
