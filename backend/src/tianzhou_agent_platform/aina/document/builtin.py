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
OUTLINE_DOCUMENT_TOOL_ID = "document.outline"
READ_DOCUMENT_SECTION_TOOL_ID = "document.read_section"
CREATE_DOCUMENT_TOOL_ID = "document.create"
UPDATE_DOCUMENT_TOOL_ID = "document.update"
UPDATE_DOCUMENT_SECTION_TOOL_ID = "document.update_section"
APPEND_DOCUMENT_TOOL_ID = "document.append"
RENAME_DOCUMENT_TOOL_ID = "document.rename"
DELETE_DOCUMENT_TOOL_ID = "document.delete"
DOCUMENT_TOOL_IDS = {
    LIST_DOCUMENTS_TOOL_ID,
    READ_DOCUMENT_TOOL_ID,
    OUTLINE_DOCUMENT_TOOL_ID,
    READ_DOCUMENT_SECTION_TOOL_ID,
    CREATE_DOCUMENT_TOOL_ID,
    UPDATE_DOCUMENT_TOOL_ID,
    UPDATE_DOCUMENT_SECTION_TOOL_ID,
    APPEND_DOCUMENT_TOOL_ID,
    RENAME_DOCUMENT_TOOL_ID,
    DELETE_DOCUMENT_TOOL_ID,
}


def document_tool_capabilities() -> list[AinaCapability]:
    name_property = {
        "type": "string",
        "description": "Markdown 文档名称；省略 .md 扩展名时会自动补充。",
    }
    content_property = {"type": "string", "description": "UTF-8 编码的 Markdown 内容。"}
    heading_property = {
        "type": "string",
        "description": "目录中返回的精确标题文字，不包含开头的 #。",
    }
    occurrence_property = {
        "type": "integer",
        "minimum": 1,
        "default": 1,
        "description": "同名标题第几次出现；默认 1。",
    }
    return [
        AinaCapability(
            id=LIST_DOCUMENTS_TOOL_ID,
            name="列出文档",
            description="列出当前用户拥有的 Markdown 文档。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        AinaCapability(
            id=OUTLINE_DOCUMENT_TOOL_ID,
            name="读取文档目录",
            description="只读取 Markdown 标题目录、行范围和 revision，不返回正文；局部编辑时先用它定位章节。",
            input_schema={
                "type": "object",
                "properties": {"name": name_property},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=READ_DOCUMENT_SECTION_TOOL_ID,
            name="读取文档章节",
            description="只读取一个 Markdown 标题及其正文，并返回用于安全更新的 revision。",
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
            name="读取文档",
            description="读取一个 Markdown 文档的完整内容和元数据；仅在任务确实需要全文时使用。",
            input_schema={
                "type": "object",
                "properties": {"name": name_property},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=CREATE_DOCUMENT_TOOL_ID,
            name="创建文档",
            description="创建新的 Markdown 文档，不会覆盖已有文件。",
            input_schema={
                "type": "object",
                "properties": {"name": name_property, "content": content_property},
                "required": ["name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=UPDATE_DOCUMENT_SECTION_TOOL_ID,
            name="更新文档章节",
            description="只替换一个 Markdown 章节。section_content 必须以同层级标题开始，revision 过期时拒绝覆盖。",
            input_schema={
                "type": "object",
                "properties": {
                    "name": name_property,
                    "heading": heading_property,
                    "occurrence": occurrence_property,
                    "section_content": {
                        "type": "string",
                        "description": "目标章节的完整 Markdown，仅包含该标题、正文及其子标题。",
                    },
                    "expected_revision": {
                        "type": "string",
                        "description": "document.read_section 返回的 revision，必须原样传入。",
                    },
                },
                "required": ["name", "heading", "section_content", "expected_revision"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=UPDATE_DOCUMENT_TOOL_ID,
            name="更新文档",
            description="替换已有 Markdown 文档的完整内容；仅用于明确要求重写全文的任务。",
            input_schema={
                "type": "object",
                "properties": {"name": name_property, "content": content_property},
                "required": ["name", "content"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=APPEND_DOCUMENT_TOOL_ID,
            name="追加文档内容",
            description="在已有文档末尾追加 Markdown 内容，不替换原内容。",
            input_schema={
                "type": "object",
                "properties": {"name": name_property, "content": content_property},
                "required": ["name", "content"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=RENAME_DOCUMENT_TOOL_ID,
            name="重命名文档",
            description="重命名已有 Markdown 文档，不改变文档内容。",
            input_schema={
                "type": "object",
                "properties": {"name": name_property, "new_name": name_property},
                "required": ["name", "new_name"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=DELETE_DOCUMENT_TOOL_ID,
            name="删除文档",
            description="经用户确认后永久删除已有 Markdown 文档。",
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
                name="文档编辑器",
                version="1.0.0",
                description="创建、读取、编辑、重命名和删除存储在 NAS 中的 Markdown 文档。",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                skills=[
                    AinaCapability(
                        id="markdown-document-management",
                        name="Markdown 文档管理",
                        description="在持久化 NAS 存储中编写和维护用户自己的 Markdown 文档。",
                        instructions=(
                            "Use document.list when the target filename is unknown. For a change limited to one "
                            "section, use document.outline to locate the exact heading when needed, then call "
                            "document.read_section and document.update_section with only that section and the "
                            "returned revision. Never use document.read or document.update for a local section "
                            "change. If the revision changed, read the section again before retrying. Use the full "
                            "document tools only when the task genuinely requires the whole document. Use "
                            "document.append only when the user explicitly wants content added at the end. Never "
                            "claim a file changed until the tool succeeds."
                        ),
                    )
                ],
                tools=document_tool_capabilities(),
                ui=[
                    AinaUiCapability(
                        id="document-editor",
                        kind="document",
                        description="由平台渲染、以 NAS 为存储的 Markdown 文件列表、编辑器和预览。",
                    )
                ],
            ),
            main_widget=WidgetDefinition(
                id="unibot-documents-main",
                kind="document",
                title="Markdown 文档编辑器",
                description="创建和编辑持久化存储在 NAS 中的 Markdown 文档。",
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
    occurrence = arguments.get("occurrence", 1)
    if not isinstance(occurrence, int) or isinstance(occurrence, bool) or occurrence < 1:
        raise PlatformError("INVALID_REQUEST", f"{tool_id} occurrence must be a positive integer")
    if tool_id == OUTLINE_DOCUMENT_TOOL_ID:
        outline = await service.get_outline(name, user_id=user_id, tenant_id=tenant_id)
        return {"outline": outline.model_dump(mode="json")}, []
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
        )
        return {"updated_section": updated_section.model_dump(mode="json")}, []
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
