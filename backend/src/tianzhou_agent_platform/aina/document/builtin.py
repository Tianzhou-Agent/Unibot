from typing import Any

from tianzhou_agent_platform.aina.document.service import DocumentService
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
from tianzhou_agent_platform.aina.protocol.widgets import WidgetDefinition
from tianzhou_agent_platform.aina.security.models import Authentication
from tianzhou_agent_platform.core.errors import PlatformError

UNIBOT_DOCUMENTS_ID = "unibot-documents"
LIST_DOCUMENTS_TOOL_ID = "document.list"
READ_DOCUMENT_TOOL_ID = "document.read"
CREATE_DOCUMENT_TOOL_ID = "document.create"
UPDATE_DOCUMENT_TOOL_ID = "document.update"
APPEND_DOCUMENT_TOOL_ID = "document.append"
RENAME_DOCUMENT_TOOL_ID = "document.rename"
DELETE_DOCUMENT_TOOL_ID = "document.delete"
DOCUMENT_TOOL_IDS = {
    LIST_DOCUMENTS_TOOL_ID,
    READ_DOCUMENT_TOOL_ID,
    CREATE_DOCUMENT_TOOL_ID,
    UPDATE_DOCUMENT_TOOL_ID,
    APPEND_DOCUMENT_TOOL_ID,
    RENAME_DOCUMENT_TOOL_ID,
    DELETE_DOCUMENT_TOOL_ID,
}


def document_tool_capabilities() -> list[AinaCapability]:
    name_property = {
        "type": "string",
        "description": "Markdown document name. The .md extension is added when omitted.",
    }
    content_property = {"type": "string", "description": "UTF-8 Markdown content."}
    return [
        AinaCapability(
            id=LIST_DOCUMENTS_TOOL_ID,
            name="List documents",
            description="List Markdown documents owned by the current user.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        AinaCapability(
            id=READ_DOCUMENT_TOOL_ID,
            name="Read document",
            description="Read the complete content and metadata of one Markdown document.",
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
            description="Create a new Markdown document. This never overwrites an existing file.",
            input_schema={
                "type": "object",
                "properties": {"name": name_property, "content": content_property},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=UPDATE_DOCUMENT_TOOL_ID,
            name="Update document",
            description="Replace the complete content of an existing Markdown document.",
            input_schema={
                "type": "object",
                "properties": {"name": name_property, "content": content_property},
                "required": ["name", "content"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=APPEND_DOCUMENT_TOOL_ID,
            name="Append to document",
            description="Append Markdown text to an existing document without replacing its current content.",
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
            description="Permanently delete an existing Markdown document after user confirmation.",
            input_schema={
                "type": "object",
                "properties": {"name": name_property},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
    ]


def unibot_documents_record() -> AinaRecord:
    return AinaRecord(
        manifest=AinaManifest(
            protocol_version="1.0",
            aina=AinaIdentity(
                id=UNIBOT_DOCUMENTS_ID,
                name="Document Editor",
                version="1.0.0",
                description=(
                    "Creates, reads, edits, renames, lists, and deletes Markdown documents stored on NAS. "
                    "Use for document writing and file-management requests."
                ),
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                skills=[
                    AinaCapability(
                        id="markdown-document-management",
                        name="Markdown document management",
                        description="Write and maintain user-owned Markdown documents in persistent NAS storage.",
                        instructions=(
                            "Use document.list when the target filename is unknown. Read the current document "
                            "before changing only part of it, then send the complete revised Markdown to "
                            "document.update. Use document.append only when the user explicitly wants content "
                            "added at the end. Never claim a file changed until the tool succeeds."
                        ),
                    )
                ],
                tools=document_tool_capabilities(),
                ui=[
                    AinaUiCapability(
                        id="document-editor",
                        kind="document",
                        description="Host-rendered Markdown file list, editor, and preview backed by NAS.",
                    )
                ],
            ),
            main_widget=WidgetDefinition(
                id="unibot-documents-main",
                kind="document",
                title="Markdown Document Editor",
                description="Create and edit Markdown documents stored in persistent NAS storage.",
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
) -> tuple[dict[str, Any], list[WidgetDefinition]]:
    name = str(arguments.get("name") or "").strip()
    if tool_id == LIST_DOCUMENTS_TOOL_ID:
        items = await service.list_documents(user_id=user_id, tenant_id=tenant_id)
        return {"count": len(items), "documents": [item.model_dump(mode="json") for item in items]}, []
    if not name:
        raise PlatformError("INVALID_REQUEST", f"{tool_id} requires name")
    if tool_id == READ_DOCUMENT_TOOL_ID:
        document = await service.get_document(name, user_id=user_id, tenant_id=tenant_id)
    elif tool_id == CREATE_DOCUMENT_TOOL_ID:
        document = await service.create_document(
            name,
            str(arguments.get("content") or ""),
            user_id=user_id,
            tenant_id=tenant_id,
        )
    elif tool_id == UPDATE_DOCUMENT_TOOL_ID:
        if "content" not in arguments:
            raise PlatformError("INVALID_REQUEST", "document.update requires content")
        document = await service.update_document(
            name,
            str(arguments["content"]),
            user_id=user_id,
            tenant_id=tenant_id,
        )
    elif tool_id == APPEND_DOCUMENT_TOOL_ID:
        if "content" not in arguments:
            raise PlatformError("INVALID_REQUEST", "document.append requires content")
        document = await service.append_document(
            name,
            str(arguments["content"]),
            user_id=user_id,
            tenant_id=tenant_id,
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
        )
    elif tool_id == DELETE_DOCUMENT_TOOL_ID:
        deleted = await service.delete_document(name, user_id=user_id, tenant_id=tenant_id)
        return {"deleted": deleted, "name": name}, []
    else:
        raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown document tool {tool_id!r}", status_code=404)
    return {"document": document.model_dump(mode="json")}, []
