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
from tianzhou_agent_platform.aina.code_runner.builtin import (
    CODE_RUNNER_TOOL_IDS,
    UNIBOT_CODE_RUNNER_ID,
    invoke_code_runner_tool,
    unibot_code_runner_record,
)
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
from tianzhou_agent_platform.aina.vision.builtin import (
    UNIBOT_IMAGE_RECOGNITION_ID,
    unibot_image_recognition_record,
)
from tianzhou_agent_platform.core.errors import (
    PlatformError,
    require_service,
    unknown_tool_error,
)
from tianzhou_agent_platform.store.errors import StorageError, StorageErrorCode, storage_error_to_platform
from tianzhou_agent_platform.core.repository import InMemoryRepository
from tianzhou_agent_platform.sandbox.service import SandboxService

BUILTIN_AINA_IDS = {
    UNIBOT_MEMORY_ID,
    UNIBOT_DOCUMENTS_ID,
    UNIBOT_SCHEDULER_ID,
    UNIBOT_CODE_RUNNER_ID,
    UNIBOT_IMAGE_RECOGNITION_ID,
}

_LEGACY_UNIBOT_ASSISTANT_ID = "unibot-assistant"


async def ensure_builtin_ainas(
    repository: InMemoryRepository,
    *,
    document_enabled: bool = False,
) -> None:
    builtins = [
        (UNIBOT_MEMORY_ID, unibot_memory_record),
        (UNIBOT_SCHEDULER_ID, unibot_scheduler_record),
        (UNIBOT_CODE_RUNNER_ID, unibot_code_runner_record),
        (UNIBOT_IMAGE_RECOGNITION_ID, unibot_image_recognition_record),
    ]
    if document_enabled:
        builtins.append((UNIBOT_DOCUMENTS_ID, unibot_documents_record))
    try:
        await repository.remove_aina(_LEGACY_UNIBOT_ASSISTANT_ID)
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
    sandbox_service: SandboxService | None = None,
) -> tuple[dict[str, Any], list[WidgetDefinition]]:
    if tool_id in CODE_RUNNER_TOOL_IDS:
        sandbox_service = require_service(
            sandbox_service,
            message="Sandbox execution is unavailable",
            source="sandbox",
        )
        return await invoke_code_runner_tool(
            sandbox_service,
            tool_id,
            arguments,
            user_id=user_id,
            tenant_id=tenant_id,
        )
    if tool_id in DOCUMENT_TOOL_IDS:
        document_service = require_service(
            document_service,
            message="Document NAS storage is unavailable",
            source="storage",
        )
        try:
            if tool_id in DOCUMENT_EDIT_TASK_TOOL_IDS:
                document_edit_task_service = require_service(
                    document_edit_task_service,
                    message="Document edit tasks are unavailable",
                    source="storage",
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
    if tool_id not in MEMORY_TOOL_IDS:
        raise unknown_tool_error(tool_id, kind="built-in")
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
    platform_error = storage_error_to_platform(error)
    return PlatformError(
        platform_error.code,
        f"{tool_id} failed: {error.message}",
        status_code=platform_error.status_code,
        retryable=platform_error.retryable,
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
    "UNIBOT_CODE_RUNNER_ID",
    "UNIBOT_IMAGE_RECOGNITION_ID",
    "ensure_builtin_ainas",
    "invoke_builtin",
    "unibot_documents_record",
    "unibot_memory_record",
    "unibot_scheduler_record",
    "unibot_code_runner_record",
    "unibot_image_recognition_record",
]
