"""Compatibility facade and dispatcher for platform-owned AINAs."""

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
from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.aina.schedule.builtin import (
    UNIBOT_SCHEDULER_ID,
    unibot_scheduler_record,
)
from tianzhou_agent_platform.aina.unibot.builtin import (
    LIST_APP_TOOL_ID,
    OPEN_AINA_TOOL_ID,
    REQUEST_CLARIFICATION_TOOL_ID,
    UNIBOT_ASSISTANT_ID,
    UNIBOT_TOOL_IDS,
    invoke_unibot_tool,
    list_app_widget,
    open_aina,
    unibot_assistant_record,
)
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.store.errors import StorageError, StorageErrorCode

BUILTIN_AINA_IDS = {
    UNIBOT_ASSISTANT_ID,
    UNIBOT_MEMORY_ID,
    UNIBOT_DOCUMENTS_ID,
    UNIBOT_SCHEDULER_ID,
}


async def ensure_unibot_assistant(
    repository: InMemoryRepository,
    *,
    document_enabled: bool = False,
) -> None:
    builtins = [
        (UNIBOT_ASSISTANT_ID, unibot_assistant_record),
        (UNIBOT_MEMORY_ID, unibot_memory_record),
        (UNIBOT_SCHEDULER_ID, unibot_scheduler_record),
    ]
    if document_enabled:
        builtins.append((UNIBOT_DOCUMENTS_ID, unibot_documents_record))
    for aina_id, factory in builtins:
        definition = factory()
        try:
            current = await repository.get_aina(aina_id)
        except PlatformError as exc:
            if exc.code != "RESOURCE_NOT_FOUND":
                raise
            await repository.register_aina(definition)
        else:
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
                )
            return await invoke_document_tool(
                document_service,
                tool_id,
                arguments,
                user_id=user_id,
                tenant_id=tenant_id,
            )
        except StorageError as exc:
            raise _document_storage_error(exc, tool_id=tool_id, arguments=arguments) from exc
    invoke = invoke_memory_tool if tool_id in MEMORY_TOOL_IDS else invoke_unibot_tool
    if tool_id not in MEMORY_TOOL_IDS | UNIBOT_TOOL_IDS:
        raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown built-in tool {tool_id!r}", status_code=404)
    return await invoke(
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
    "LIST_APP_TOOL_ID",
    "OPEN_AINA_TOOL_ID",
    "RECALL_TOOL_ID",
    "REMEMBER_TOOL_ID",
    "UPDATE_TOOL_ID",
    "REQUEST_CLARIFICATION_TOOL_ID",
    "UNIBOT_ASSISTANT_ID",
    "UNIBOT_DOCUMENTS_ID",
    "UNIBOT_MEMORY_ID",
    "UNIBOT_SCHEDULER_ID",
    "ensure_unibot_assistant",
    "invoke_builtin",
    "list_app_widget",
    "open_aina",
    "unibot_assistant_record",
    "unibot_documents_record",
    "unibot_memory_record",
    "unibot_scheduler_record",
]
