from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.tasks.models import TaskCreateRequest, TaskDeleteRequest, TaskUpdateRequest
from tianzhou_agent_platform.tasks.service import TaskService

TASK_CREATE_TOOL_ID = "task_create"
TASK_UPDATE_TOOL_ID = "task_update"
TASK_QUERY_TOOL_ID = "task_query"
TASK_DELETE_TOOL_ID = "task_delete"
TASK_TOOL_IDS = {
    TASK_CREATE_TOOL_ID,
    TASK_UPDATE_TOOL_ID,
    TASK_QUERY_TOOL_ID,
    TASK_DELETE_TOOL_ID,
}


def task_tool_specs() -> list[dict[str, Any]]:
    create_item = {
        "type": "object",
        "properties": {
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "description": {"type": "string"},
            "parent_task_id": {"type": "string"},
            "client_ref": {"type": "string", "minLength": 1, "maxLength": 80},
            "parent_ref": {"type": "string", "minLength": 1, "maxLength": 80},
        },
        "required": ["title"],
        "additionalProperties": False,
    }
    return [
        {
            "id": TASK_CREATE_TOOL_ID,
            "display_name": "Create structured tasks",
            "description": (
                "Create one task or a batch of up to 20 tasks in the current session's three-level task tree. "
                "Use tasks only for meaningful deliverables or phases, not for individual file reads, searches, "
                "or tool calls. Prefer progressive planning. In a batch, connect items with client_ref/parent_ref."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    **create_item["properties"],
                    "tasks": {"type": "array", "minItems": 1, "maxItems": 20, "items": create_item},
                },
                "additionalProperties": False,
            },
        },
        {
            "id": TASK_UPDATE_TOOL_ID,
            "display_name": "Update structured task",
            "description": (
                "Update a current-session task using expected_version. Only leaf task status is writable. Set "
                "status=verifying to request completion; the runtime Completion Gate alone can write completed. "
                "A normal tool error or failed test should stay in_progress unless the task is unrecoverable."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_id": {"type": "string", "minLength": 1},
                    "expected_version": {"type": "integer", "minimum": 1},
                    "title": {"type": "string", "minLength": 1, "maxLength": 200},
                    "description": {"type": "string"},
                    "parent_task_id": {
                        "description": "New parent task id, or null to move the leaf to the root level."
                    },
                    "status": {
                        "type": "string",
                        "enum": ["pending", "in_progress", "verifying", "skipped", "failed"],
                    },
                    "reason": {"type": "string", "maxLength": 500},
                    "evidence": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["task_id", "expected_version"],
                "additionalProperties": False,
            },
        },
        {
            "id": TASK_QUERY_TOOL_ID,
            "display_name": "Query structured tasks",
            "description": (
                "Read the complete structured task tree, revision, statuses, and optimistic-lock versions for "
                "the current session. Use it before an update when the current version is unknown or conflicted."
            ),
            "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        {
            "id": TASK_DELETE_TOOL_ID,
            "display_name": "Delete pending structured tasks",
            "description": (
                "Physically delete only pending tasks or fully pending subtrees in the current session. Use "
                "status=skipped instead for work that started or is retained as history."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "task_ids": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 20,
                        "items": {"type": "string", "minLength": 1},
                    }
                },
                "required": ["task_ids"],
                "additionalProperties": False,
            },
        },
    ]


async def invoke_task_operation(
    service: TaskService,
    tool_id: str,
    arguments: dict[str, Any],
    *,
    user_id: str,
    tenant_id: str,
    session_id: str,
    tool_execution_id: str,
) -> dict[str, Any]:
    try:
        if tool_id == TASK_CREATE_TOOL_ID:
            result = await service.create(
                session_id,
                TaskCreateRequest.model_validate(arguments),
                user_id=user_id,
                tenant_id=tenant_id,
                tool_execution_id=tool_execution_id,
            )
            return result.model_dump(mode="json")
        if tool_id == TASK_UPDATE_TOOL_ID:
            result = await service.update(
                session_id,
                TaskUpdateRequest.model_validate(arguments),
                user_id=user_id,
                tenant_id=tenant_id,
            )
            return result.model_dump(mode="json")
        if tool_id == TASK_QUERY_TOOL_ID:
            result = await service.query(session_id, user_id=user_id, tenant_id=tenant_id)
            return result.model_dump(mode="json")
        if tool_id == TASK_DELETE_TOOL_ID:
            result = await service.delete(
                session_id,
                TaskDeleteRequest.model_validate(arguments),
                user_id=user_id,
                tenant_id=tenant_id,
            )
            return result.model_dump(mode="json")
    except ValidationError as exc:
        raise PlatformError(
            "INVALID_REQUEST",
            f"Invalid {tool_id} arguments: {exc}",
            source="task",
        ) from exc
    raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown task tool {tool_id!r}", status_code=404)
