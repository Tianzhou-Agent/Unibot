"""Registration and dispatcher for platform-owned AINAs."""

from typing import Any

from tianzhou_agent_platform.aina.document.builtin import (
    DOCUMENT_EDIT_TASK_TOOL_IDS,
    DOCUMENT_TOOL_IDS,
    UNIBOT_DOCUMENTS_ID,
    invoke_document_edit_task_tool,
    invoke_document_tool,
    unibot_documents_record,
)
from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.aina.document.task_service import DocumentEditTaskService
from tianzhou_agent_platform.aina.memory.builtin import (
    FORGET_TOOL_ID,
    MEMORY_TOOL_IDS,
    RECALL_TOOL_ID,
    REMEMBER_TOOL_ID,
    UPDATE_TOOL_ID,
    UNIBOT_MEMORY_ID,
    invoke_memory_tool,
    unibot_memory_record,
)
from tianzhou_agent_platform.aina.package.models import PackageRuntimeDefinition
from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.aina.schedule.builtin import (
    UNIBOT_SCHEDULER_ID,
    unibot_scheduler_record,
)
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository
from tianzhou_agent_platform.store.errors import StorageError, StorageErrorCode

BUILTIN_AINA_IDS = {
    UNIBOT_MEMORY_ID,
    UNIBOT_DOCUMENTS_ID,
    UNIBOT_SCHEDULER_ID,
}

# Retired built-ins; these apps are now manually installed examples.
_LEGACY_AINA_IDS = ("unibot-assistant", "unibot-image-recognition")


async def ensure_builtin_ainas(
    repository: InMemoryRepository,
    *,
    document_enabled: bool = False,
) -> None:
    builtins = [
        (UNIBOT_MEMORY_ID, unibot_memory_record),
        (UNIBOT_SCHEDULER_ID, unibot_scheduler_record),
    ]
    if document_enabled:
        builtins.append((UNIBOT_DOCUMENTS_ID, unibot_documents_record))
    # Remove only the old automatic registrations, preserving explicitly added packages.
    for aina_id in ("unibot-code-runner", "unibot.image-recognition"):
        try:
            record = await repository.get_aina(aina_id)
        except PlatformError as exc:
            if exc.code != "RESOURCE_NOT_FOUND":
                raise
            continue
        runtime = record.manifest.runtime
        if runtime.type == "builtin" or (isinstance(runtime, PackageRuntimeDefinition) and runtime.bundled):
            await repository.remove_aina(aina_id)
    for legacy_id in _LEGACY_AINA_IDS:
        try:
            await repository.remove_aina(legacy_id)
        except PlatformError as exc:
            if exc.code != "RESOURCE_NOT_FOUND":
                raise
    for aina_id, factory in builtins:
        definition = factory()
        try:
            current = await repository.get_aina(aina_id)
        except PlatformError as exc:
            if exc.code != "RESOURCE_NOT_FOUND":
                raise
            try:
                await repository.register_aina(definition)
                continue
            except PlatformError as registered:
                # Another replica starting at the same time registered it first.
                if registered.code != "CONFLICT":
                    raise
                current = await repository.get_aina(aina_id)
        await repository.upsert_aina(
            definition.model_copy(update={"registered_at": current.registered_at}, deep=True)
        )


async def invoke_builtin(
    repository: InMemoryRepository,
    tool_id: str,
    arguments: dict[str, Any],
    *,
    user_id: str,
    tenant_id: str,
    conversation_id: str,
    document_service: DocumentService | None = None,
    document_edit_task_service: DocumentEditTaskService | None = None,
) -> tuple[dict[str, Any], list[WidgetDefinition]]:
    if tool_id in DOCUMENT_TOOL_IDS:
        if document_service is None:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "Document NAS storage is unavailable",
                status_code=503,
                source="storage",
            )
        conversation = await repository.require_conversation_actor(
            conversation_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )
        workspace_id = conversation.workspace_id
        workspace_storage_key = None
        if workspace_id is not None:
            workspace = await repository.require_workspace_actor(
                workspace_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            workspace_storage_key = workspace.storage_key
        try:
            if tool_id in DOCUMENT_EDIT_TASK_TOOL_IDS:
                if document_edit_task_service is None:
                    raise PlatformError(
                        "DEPENDENCY_FAILED",
                        "Document edit tasks are unavailable",
                        status_code=503,
                    )
                return await invoke_document_edit_task_tool(
                    document_edit_task_service,
                    tool_id,
                    arguments,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    workspace_id=workspace_id,
                )
            return await invoke_document_tool(
                document_service,
                tool_id,
                arguments,
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_storage_key=workspace_storage_key,
            )
        except StorageError as exc:
            raise _document_storage_error(exc, tool_id=tool_id, arguments=arguments) from exc
    if tool_id not in MEMORY_TOOL_IDS:
        raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown built-in tool {tool_id!r}", status_code=404)
    return await invoke_memory_tool(
        repository,
        tool_id,
        arguments,
        user_id=user_id,
        tenant_id=tenant_id,
        conversation_id=conversation_id,
    )


def _document_storage_error(
    error: StorageError,
    *,
    tool_id: str,
    arguments: dict[str, Any],
) -> PlatformError:
    if error.code == StorageErrorCode.NOT_FOUND:
        name = str(arguments.get("name") or "").strip()
        return PlatformError(
            "RESOURCE_NOT_FOUND",
            f"Document {name!r} was not found. Call document.list to refresh available filenames before retrying.",
            status_code=404,
            source="storage",
        )
    code, status_code, retryable = {
        StorageErrorCode.VALIDATION_FAILURE: ("INVALID_REQUEST", 422, False),
        StorageErrorCode.POLICY_VIOLATION: ("PERMISSION_DENIED", 403, False),
        StorageErrorCode.TIMEOUT: ("TIMEOUT", 504, True),
        StorageErrorCode.BACKEND_UNAVAILABLE: ("DEPENDENCY_FAILED", 503, True),
        StorageErrorCode.UNSUPPORTED_CAPABILITY: ("DEPENDENCY_FAILED", 501, False),
        StorageErrorCode.UNKNOWN_BACKEND_FAILURE: ("DEPENDENCY_FAILED", 500, True),
    }[error.code]
    return PlatformError(
        code,
        f"{tool_id} failed: {error.message}",
        status_code=status_code,
        retryable=retryable,
        source="storage",
    )


__all__ = [
    "BUILTIN_AINA_IDS",
    "DOCUMENT_TOOL_IDS",
    "FORGET_TOOL_ID",
    "RECALL_TOOL_ID",
    "REMEMBER_TOOL_ID",
    "UPDATE_TOOL_ID",
    "UNIBOT_DOCUMENTS_ID",
    "UNIBOT_MEMORY_ID",
    "UNIBOT_SCHEDULER_ID",
    "ensure_builtin_ainas",
    "invoke_builtin",
    "unibot_documents_record",
    "unibot_memory_record",
    "unibot_scheduler_record",
]
