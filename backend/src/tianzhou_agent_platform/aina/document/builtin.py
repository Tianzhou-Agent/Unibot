from typing import Any

from tianzhou_agent_platform.aina.document.service import DocumentService
from tianzhou_agent_platform.aina.document.task_models import (
    DocumentEditTaskCreate,
    DocumentSectionSelection,
)
from tianzhou_agent_platform.aina.document.task_service import DocumentEditTaskService
from tianzhou_agent_platform.aina.protocol.models import (
    AinaCapabilities,
    AinaCapability,
    AinaIdentity,
    AinaManifest,
    AinaRecord,
    AinaUiCapability,
    BuiltinRuntimeDefinition,
    Publisher,
)
from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition, WidgetDocumentSection
from tianzhou_agent_platform.aina.security.models import Authentication
from tianzhou_agent_platform.core.errors import PlatformError

UNIBOT_DOCUMENTS_ID = "unibot-documents"
LIST_DOCUMENTS_TOOL_ID = "document.list"
SEARCH_DOCUMENTS_TOOL_ID = "document.search"
READ_DOCUMENT_TOOL_ID = "document.read"
OUTLINE_DOCUMENT_TOOL_ID = "document.outline"
BROWSE_DOCUMENT_TOOL_ID = "document.browse"
READ_DOCUMENT_SECTION_TOOL_ID = "document.read_section"
CREATE_DOCUMENT_TOOL_ID = "document.create"
UPDATE_DOCUMENT_SECTION_TOOL_ID = "document.update_section"
APPEND_DOCUMENT_TOOL_ID = "document.append"
RENAME_DOCUMENT_TOOL_ID = "document.rename"
DELETE_DOCUMENT_TOOL_ID = "document.delete"
CREATE_EDIT_TASK_TOOL_ID = "document.edit_task.create"
LIST_EDIT_TASKS_TOOL_ID = "document.edit_task.list"
READ_EDIT_TASK_TOOL_ID = "document.edit_task.read"
UPDATE_DRAFT_TOOL_ID = "document.edit_task.update_draft"
AI_REVISE_DRAFT_TOOL_ID = "document.edit_task.ai_revise"
RETRY_EDIT_TASK_TOOL_ID = "document.edit_task.retry"
MERGE_EDIT_SECTION_TOOL_ID = "document.edit_task.merge_section"
ABANDON_EDIT_SECTION_TOOL_ID = "document.edit_task.abandon_section"
DOCUMENT_EDIT_TASK_TOOL_IDS = {
    CREATE_EDIT_TASK_TOOL_ID,
    LIST_EDIT_TASKS_TOOL_ID,
    READ_EDIT_TASK_TOOL_ID,
    UPDATE_DRAFT_TOOL_ID,
    AI_REVISE_DRAFT_TOOL_ID,
    RETRY_EDIT_TASK_TOOL_ID,
    MERGE_EDIT_SECTION_TOOL_ID,
    ABANDON_EDIT_SECTION_TOOL_ID,
}
DOCUMENT_TOOL_IDS = {
    LIST_DOCUMENTS_TOOL_ID,
    SEARCH_DOCUMENTS_TOOL_ID,
    READ_DOCUMENT_TOOL_ID,
    OUTLINE_DOCUMENT_TOOL_ID,
    BROWSE_DOCUMENT_TOOL_ID,
    READ_DOCUMENT_SECTION_TOOL_ID,
    CREATE_DOCUMENT_TOOL_ID,
    UPDATE_DOCUMENT_SECTION_TOOL_ID,
    APPEND_DOCUMENT_TOOL_ID,
    RENAME_DOCUMENT_TOOL_ID,
    DELETE_DOCUMENT_TOOL_ID,
    *DOCUMENT_EDIT_TASK_TOOL_IDS,
}


def document_tool_capabilities() -> list[AinaCapability]:
    name_property = {
        "type": "string",
        "description": "Markdown document name; the .md extension is added when omitted.",
        "minLength": 1,
        "maxLength": 500,
    }
    content_property = {
        "type": "string",
        "description": "UTF-8 Markdown content.",
        "maxLength": 2_000_000,
    }
    heading_property = {
        "type": "string",
        "description": "Exact heading text as returned by the outline, without the leading #.",
        "minLength": 1,
        "maxLength": 500,
    }
    occurrence_property = {
        "type": "integer",
        "minimum": 1,
        "default": 1,
        "description": "Which occurrence of a repeated heading; defaults to 1.",
    }
    capabilities = [
        AinaCapability(
            id=LIST_DOCUMENTS_TOOL_ID,
            name="List documents",
            description="List every Markdown document. Use only when the user wants to browse all files and gave no name, topic or keyword.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        AinaCapability(
            id=SEARCH_DOCUMENTS_TOOL_ID,
            name="Search documents",
            description=(
                "Search the file names and bodies of the current user's Markdown documents by keyword. Prefer this "
                "tool whenever the user gives a name, topic, heading or content keyword; do not list every "
                "document first."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The document name, topic or keyword the user gave; do not rewrite it as 'all documents'.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=BROWSE_DOCUMENT_TOOL_ID,
            name="Browse document sections",
            description=(
                "Use when the user wants to see a document's outline or section structure, or pick a section to "
                "read. Returns an interactive section navigation widget; do not repeat the headings in the "
                "text answer."
            ),
            input_schema={
                "type": "object",
                "properties": {"name": name_property},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=OUTLINE_DOCUMENT_TOOL_ID,
            name="Read document outline",
            description="Read only the Markdown heading outline, line ranges and revision, without body text. Use it first to locate a section before a partial edit.",
            input_schema={
                "type": "object",
                "properties": {"name": name_property},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=READ_DOCUMENT_SECTION_TOOL_ID,
            name="Read document section",
            description="Read one Markdown heading and its body, and return the revision needed for a safe update.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": name_property,
                    "heading": heading_property,
                    "occurrence": occurrence_property,
                },
                "required": ["name", "heading"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=READ_DOCUMENT_TOOL_ID,
            name="Read document",
            description="Read the full content and metadata of one Markdown document. Use only when the task really needs the whole text.",
            input_schema={
                "type": "object",
                "properties": {"name": name_property},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=CREATE_DOCUMENT_TOOL_ID,
            name="Create document",
            description="Create a new Markdown document; never overwrites an existing file.",
            input_schema={
                "type": "object",
                "properties": {"name": name_property, "content": content_property},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=UPDATE_DOCUMENT_SECTION_TOOL_ID,
            name="Update document section",
            description="Replace exactly one Markdown section. section_content must start with a heading of the same level; the update is rejected when the revision is stale.",
            input_schema={
                "type": "object",
                "properties": {
                    "name": name_property,
                    "heading": heading_property,
                    "occurrence": occurrence_property,
                    "section_content": {
                        "type": "string",
                        "description": "The complete Markdown of the target section: only its heading, body and sub-headings.",
                    },
                    "expected_revision": {
                        "type": "string",
                        "description": "The revision returned by document.read_section, passed unchanged.",
                    },
                },
                "required": ["name", "heading", "section_content", "expected_revision"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=APPEND_DOCUMENT_TOOL_ID,
            name="Append to document",
            description="Append Markdown content to the end of an existing document without replacing it.",
            input_schema={
                "type": "object",
                "properties": {"name": name_property, "content": content_property},
                "required": ["name", "content"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=RENAME_DOCUMENT_TOOL_ID,
            name="Rename document",
            description="Rename an existing Markdown document without changing its content.",
            input_schema={
                "type": "object",
                "properties": {"name": name_property, "new_name": name_property},
                "required": ["name", "new_name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=DELETE_DOCUMENT_TOOL_ID,
            name="Delete document",
            description="Permanently delete an existing Markdown document after the user confirms.",
            input_schema={
                "type": "object",
                "properties": {"name": name_property},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=CREATE_EDIT_TASK_TOOL_ID,
            name="Create document edit task",
            description=(
                "Create an asynchronous reviewed edit task for one or more non-overlapping sections. "
                "Use document.outline first to obtain exact heading and occurrence values. The formal document "
                "is not changed until a reviewed section is explicitly merged."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": name_property,
                    "description": {
                        "type": "string",
                        "description": "The user's complete editing request. The task title is generated automatically.",
                        "minLength": 1,
                        "maxLength": 20_000,
                    },
                    "sections": {
                        "type": "array",
                        "minItems": 1,
                        "maxItems": 50,
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": heading_property,
                                "occurrence": occurrence_property,
                            },
                            "required": ["heading"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "description", "sections"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=LIST_EDIT_TASKS_TOOL_ID,
            name="List document edit tasks",
            description="List reviewed edit tasks for a document, including status, section ids, and draft revisions.",
            input_schema={
                "type": "object",
                "properties": {"name": name_property},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=READ_EDIT_TASK_TOOL_ID,
            name="Read document edit task",
            description="Read one edit task and its reviewable section drafts by exact task_id.",
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=UPDATE_DRAFT_TOOL_ID,
            name="Update section draft",
            description=(
                "Replace one review draft after the user directly edits or dictates its complete Markdown. "
                "Pass the current draft_revision to prevent overwriting a newer draft."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "section_id": {"type": "string"},
                    "content": content_property,
                    "expected_draft_revision": {"type": "integer", "minimum": 0},
                },
                "required": ["task_id", "section_id", "content", "expected_draft_revision"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=AI_REVISE_DRAFT_TOOL_ID,
            name="Revise section draft with AI",
            description=(
                "Queue an asynchronous AI revision for one existing section draft. Read the task first and pass "
                "the current draft_revision. This still does not change the formal document."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "task_id": {"type": "string"},
                    "section_id": {"type": "string"},
                    "instruction": {"type": "string"},
                    "expected_draft_revision": {"type": "integer", "minimum": 0},
                },
                "required": ["task_id", "section_id", "instruction", "expected_draft_revision"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=RETRY_EDIT_TASK_TOOL_ID,
            name="Retry document edit task",
            description="Retry failed draft generation for an edit task.",
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=MERGE_EDIT_SECTION_TOOL_ID,
            name="Merge section draft",
            description=(
                "Merge one reviewed section draft into the formal document. Only call after the user explicitly "
                "chooses to merge that section; the platform will request confirmation before changing the document."
            ),
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}, "section_id": {"type": "string"}},
                "required": ["task_id", "section_id"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=ABANDON_EDIT_SECTION_TOOL_ID,
            name="Abandon section draft",
            description="Abandon one reviewed section draft without changing the formal document.",
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}, "section_id": {"type": "string"}},
                "required": ["task_id", "section_id"],
                "additionalProperties": False,
            },
        ),
    ]
    return capabilities


def unibot_documents_record() -> AinaRecord:
    return AinaRecord(
        manifest=AinaManifest(
            protocol_version="1.0",
            aina=AinaIdentity(
                id=UNIBOT_DOCUMENTS_ID,
                name="Document Editor",
                version="1.0.0",
                description="Create, read, edit, rename and delete Markdown documents stored on NAS.",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                skills=[
                    AinaCapability(
                        id="markdown-document-management",
                        name="Markdown document management",
                        description="Write and maintain the user's own Markdown documents on persistent NAS storage.",
                        instructions=(
                            "Use document.search when the user provides a filename, topic, title, or content "
                            "keyword. Preserve that query instead of replacing it with a request for all documents. "
                            "Use document.list only when the user wants to browse every document without a search "
                            "term. Use document.outline and "
                            "document.read_section to inspect only the relevant content. The editor has two modes. "
                            "When the user explicitly asks to directly edit, immediately save, or use edit mode, "
                            "only use document.update_section. Full-document replacement is not supported. Always "
                            "read the target section first and pass its revision. When the "
                            "user asks to create a task, generate a draft, review before saving, or use task mode, use "
                            "document.edit_task.create with exact non-overlapping headings. Task drafts are "
                            "asynchronous and must be reviewed. "
                            "Use document.edit_task.read or list to report progress, update_draft for user-authored "
                            "draft changes, ai_revise for requested AI changes, and retry after failures. Only use "
                            "document.edit_task.merge_section after the user explicitly chooses to merge that "
                            "chapter, or abandon_section when they reject it. Never claim the formal document "
                            "changed before a section merge completes. When the user "
                            "wants to view the outline, "
                            "browse chapters, or choose a section to read, use document.browse. It attaches an "
                            "interactive chapter widget, so keep the text response to one short sentence and never "
                            "transcribe the headings into Markdown."
                        ),
                    )
                ],
                tools=document_tool_capabilities(),
                ui=[
                    AinaUiCapability(
                        id="document-editor",
                        kind="document",
                        description="Platform-rendered Markdown file list, editor and preview backed by NAS storage.",
                    ),
                    AinaUiCapability(
                        id="document-outline",
                        kind="document_outline",
                        description="Browse a document by heading level in the chat and read single sections on demand.",
                    ),
                ],
            ),
            main_widget=WidgetDefinition(
                id="unibot-documents-main",
                kind="document",
                title="Markdown Document Editor",
                description="Create and edit Markdown documents persisted on NAS.",
            ),
            authentication=Authentication(type="none"),
        ),
        last_health={"status": "healthy", "runtime": "builtin", "storage": "nas"},
    )


async def invoke_document_tool(
    service: DocumentService,
    tool_id: str,
    arguments: dict[str, Any],
    *,
    user_id: str,
    tenant_id: str,
    workspace_storage_key: str | None = None,
) -> tuple[dict[str, Any], list[WidgetDefinition]]:
    name = str(arguments.get("name") or "").strip()
    if tool_id == LIST_DOCUMENTS_TOOL_ID:
        items = await service.list_documents(
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return {"count": len(items), "documents": [item.model_dump(mode="json") for item in items]}, []
    if tool_id == SEARCH_DOCUMENTS_TOOL_ID:
        query = str(arguments.get("query") or "").strip()
        if not query:
            raise PlatformError("INVALID_REQUEST", "document.search requires query")
        matches = await service.search_documents(
            query,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return {
            "query": query,
            "count": len(matches),
            "matches": [item.model_dump(mode="json") for item in matches],
        }, []
    if not name:
        raise PlatformError("INVALID_REQUEST", f"{tool_id} requires name")
    occurrence = arguments.get("occurrence", 1)
    if not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 1:
        raise PlatformError("INVALID_REQUEST", f"{tool_id} occurrence must be a positive integer")
    if tool_id == OUTLINE_DOCUMENT_TOOL_ID:
        outline = await service.get_outline(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return {"outline": outline.model_dump(mode="json")}, []
    if tool_id == BROWSE_DOCUMENT_TOOL_ID:
        outline = await service.get_outline(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        levels = [heading.level for heading in outline.headings]
        root_level = min(levels, default=1)
        chapter_level = root_level + 1 if root_level + 1 in levels else root_level
        chapter_count = sum(heading.level == chapter_level for heading in outline.headings)
        widget = WidgetDefinition(
            id=f"document-outline-{outline.revision[:16]}",
            kind="document_outline",
            title=outline.name,
            description="Pick a section to view its content; no need to send another message.",
            document_name=outline.name,
            sections=[
                WidgetDocumentSection(**heading.model_dump())
                for heading in outline.headings
            ],
        )
        return (
            {
                "document": {
                    "name": outline.name,
                    "size_bytes": outline.size_bytes,
                    "revision": outline.revision,
                },
                "chapter_count": chapter_count,
                "heading_count": len(outline.headings),
                "presentation": "interactive_document_outline_widget",
                "response_instruction": (
                    "Reply with one short sentence, in the user's language, saying the section navigation is "
                    "loaded and the user can pick a section in the widget below. Do not add headings, counts, "
                    "or other explanation."
                ),
            },
            [widget],
        )
    if tool_id == READ_DOCUMENT_SECTION_TOOL_ID:
        heading = str(arguments.get("heading") or "").strip()
        if not heading:
            raise PlatformError("INVALID_REQUEST", "document.read_section requires heading")
        section = await service.get_section(
            name,
            heading,
            occurrence,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return {"section": section.model_dump(mode="json")}, []
    if tool_id == UPDATE_DOCUMENT_SECTION_TOOL_ID:
        heading = str(arguments.get("heading") or "").strip()
        if not heading or "section_content" not in arguments or "expected_revision" not in arguments:
            raise PlatformError(
                "INVALID_REQUEST",
                "document.update_section requires heading, section_content, and expected_revision",
            )
        updated_section = await service.update_section(
            name,
            heading,
            occurrence,
            str(arguments["section_content"]),
            str(arguments["expected_revision"]),
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return {"updated_section": updated_section.model_dump(mode="json")}, []
    if tool_id == READ_DOCUMENT_TOOL_ID:
        document = await service.get_document(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
    elif tool_id == CREATE_DOCUMENT_TOOL_ID:
        document = await service.create_document(
            name,
            str(arguments.get("content") or ""),
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
    elif tool_id == APPEND_DOCUMENT_TOOL_ID:
        if "content" not in arguments:
            raise PlatformError("INVALID_REQUEST", "document.append requires content")
        document = await service.append_document(
            name,
            str(arguments["content"]),
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
    elif tool_id == RENAME_DOCUMENT_TOOL_ID:
        new_name = str(arguments.get("new_name") or "").strip()
        if not new_name:
            raise PlatformError("INVALID_REQUEST", "document.rename requires new_name")
        document = await service.rename_document(
            name,
            new_name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
    elif tool_id == DELETE_DOCUMENT_TOOL_ID:
        deleted = await service.delete_document(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_storage_key=workspace_storage_key,
        )
        return {"deleted": deleted, "name": name}, []
    else:
        raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown document tool {tool_id!r}", status_code=404)
    return {"document": document.model_dump(mode="json")}, []


async def invoke_document_edit_task_tool(
    service: DocumentEditTaskService,
    tool_id: str,
    arguments: dict[str, Any],
    *,
    user_id: str,
    tenant_id: str,
    workspace_id: str | None = None,
) -> tuple[dict[str, Any], list[WidgetDefinition]]:
    if tool_id == CREATE_EDIT_TASK_TOOL_ID:
        name = _required_string(arguments, "name", tool_id)
        description = _required_string(arguments, "description", tool_id)
        raw_sections = arguments.get("sections")
        if not isinstance(raw_sections, list) or not raw_sections:
            raise PlatformError("INVALID_REQUEST", f"{tool_id} requires at least one section")
        sections = [DocumentSectionSelection.model_validate(item) for item in raw_sections]
        task = await service.create_task(
            name,
            DocumentEditTaskCreate(
                user_id=user_id,
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                description=description,
                sections=sections,
            ),
        )
        return {"task": task.model_dump(mode="json")}, []

    if tool_id == LIST_EDIT_TASKS_TOOL_ID:
        name = _required_string(arguments, "name", tool_id)
        tasks = await service.list_tasks(
            name,
            user_id=user_id,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        return {"count": len(tasks), "tasks": [task.model_dump(mode="json") for task in tasks]}, []

    task_id = _required_string(arguments, "task_id", tool_id)
    if tool_id not in {
        READ_EDIT_TASK_TOOL_ID,
        UPDATE_DRAFT_TOOL_ID,
        AI_REVISE_DRAFT_TOOL_ID,
        RETRY_EDIT_TASK_TOOL_ID,
        MERGE_EDIT_SECTION_TOOL_ID,
        ABANDON_EDIT_SECTION_TOOL_ID,
    }:
        raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown document edit task tool {tool_id!r}", status_code=404)
    task = await service.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
    if task.workspace_id != workspace_id:
        raise PlatformError(
            "CONFLICT",
            "Document edit task does not belong to the current workspace",
            status_code=409,
        )
    if tool_id == READ_EDIT_TASK_TOOL_ID:
        return {"task": task.model_dump(mode="json")}, []
    if tool_id == UPDATE_DRAFT_TOOL_ID:
        section_id = _required_string(arguments, "section_id", tool_id)
        if "content" not in arguments:
            raise PlatformError("INVALID_REQUEST", f"{tool_id} requires content")
        task = await service.update_draft(
            task_id,
            section_id,
            str(arguments["content"]),
            _required_revision(arguments, tool_id),
            user_id=user_id,
            tenant_id=tenant_id,
        )
    elif tool_id == AI_REVISE_DRAFT_TOOL_ID:
        task = await service.request_ai_revision(
            task_id,
            _required_string(arguments, "section_id", tool_id),
            _required_string(arguments, "instruction", tool_id),
            _required_revision(arguments, tool_id),
            user_id=user_id,
            tenant_id=tenant_id,
        )
    elif tool_id == RETRY_EDIT_TASK_TOOL_ID:
        task = await service.retry_failed(task_id, user_id=user_id, tenant_id=tenant_id)
    elif tool_id == MERGE_EDIT_SECTION_TOOL_ID:
        task = await service.merge_section(
            task_id,
            _required_string(arguments, "section_id", tool_id),
            user_id=user_id,
            tenant_id=tenant_id,
        )
    elif tool_id == ABANDON_EDIT_SECTION_TOOL_ID:
        task = await service.abandon_section(
            task_id,
            _required_string(arguments, "section_id", tool_id),
            user_id=user_id,
            tenant_id=tenant_id,
        )
    return {"task": task.model_dump(mode="json")}, []


def _required_string(arguments: dict[str, Any], key: str, tool_id: str) -> str:
    value = str(arguments.get(key) or "").strip()
    if not value:
        raise PlatformError("INVALID_REQUEST", f"{tool_id} requires {key}")
    return value


def _required_revision(arguments: dict[str, Any], tool_id: str) -> int:
    value = arguments.get("expected_draft_revision")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise PlatformError(
            "INVALID_REQUEST",
            f"{tool_id} expected_draft_revision must be a non-negative integer",
        )
    return value
