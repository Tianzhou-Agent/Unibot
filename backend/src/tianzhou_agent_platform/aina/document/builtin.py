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
MERGE_EDIT_TASK_TOOL_ID = "document.edit_task.merge"
DOCUMENT_EDIT_TASK_TOOL_IDS = {
    CREATE_EDIT_TASK_TOOL_ID,
    LIST_EDIT_TASKS_TOOL_ID,
    READ_EDIT_TASK_TOOL_ID,
    UPDATE_DRAFT_TOOL_ID,
    AI_REVISE_DRAFT_TOOL_ID,
    RETRY_EDIT_TASK_TOOL_ID,
    MERGE_EDIT_TASK_TOOL_ID,
}
DOCUMENT_TOOL_IDS = {
    LIST_DOCUMENTS_TOOL_ID,
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
    capabilities = [
        AinaCapability(
            id=LIST_DOCUMENTS_TOOL_ID,
            name="列出文档",
            description="列出当前用户拥有的 Markdown 文档。",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        AinaCapability(
            id=BROWSE_DOCUMENT_TOOL_ID,
            name="浏览文档章节",
            description=(
                "当用户想查看文档目录、章节结构或选择章节阅读时使用。返回交互式章节导航组件，"
                "不要在文字回答中重复罗列标题。"
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
        AinaCapability(
            id=CREATE_EDIT_TASK_TOOL_ID,
            name="创建文档修改任务",
            description=(
                "Create an asynchronous reviewed edit task for one or more non-overlapping sections. "
                "Use document.outline first to obtain exact heading and occurrence values. The formal document "
                "is not changed until document.edit_task.merge is confirmed."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "name": name_property,
                    "description": {
                        "type": "string",
                        "description": "The user's complete editing request. The task title is generated automatically.",
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
            name="列出文档修改任务",
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
            name="读取文档修改任务",
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
            name="修改章节草稿",
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
            name="让 AI 继续修改章节草稿",
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
            name="重试文档修改任务",
            description="Retry failed draft generation for an edit task.",
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
                "additionalProperties": False,
            },
        ),
        AinaCapability(
            id=MERGE_EDIT_TASK_TOOL_ID,
            name="合并文档修改任务",
            description=(
                "Merge every reviewed draft in a task into the formal document. Only call after the user explicitly "
                "chooses to merge; the platform will request confirmation before changing the document."
            ),
            input_schema={
                "type": "object",
                "properties": {"task_id": {"type": "string"}},
                "required": ["task_id"],
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
                            "Use document.list when the target filename is unknown. Use document.outline and "
                            "document.read_section to inspect only the relevant content. The editor has two modes. "
                            "When the user explicitly asks to directly edit, immediately save, or use edit mode, "
                            "only use document.update_section. Full-document replacement is not supported. Always "
                            "read the target section first and pass its revision. When the "
                            "user asks to create a task, generate a draft, review before saving, or use task mode, use "
                            "document.edit_task.create with exact non-overlapping headings. Task drafts are "
                            "asynchronous and must be reviewed. "
                            "Use document.edit_task.read or list to report progress, update_draft for user-authored "
                            "draft changes, ai_revise for requested AI changes, and retry after failures. Only use "
                            "document.edit_task.merge after the user explicitly chooses to merge; never claim the "
                            "formal document changed before merge completes. When the user "
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
                        description="由平台渲染、以 NAS 为存储的 Markdown 文件列表、编辑器和预览。",
                    ),
                    AinaUiCapability(
                        id="document-outline",
                        kind="document_outline",
                        description="在对话中按标题层级浏览文档，并按需读取单个章节。",
                    ),
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
    if tool_id == BROWSE_DOCUMENT_TOOL_ID:
        outline = await service.get_outline(name, user_id=user_id, tenant_id=tenant_id)
        levels = [heading.level for heading in outline.headings]
        root_level = min(levels, default=1)
        chapter_level = root_level + 1 if root_level + 1 in levels else root_level
        chapter_count = sum(heading.level == chapter_level for heading in outline.headings)
        widget = WidgetDefinition(
            id=f"document-outline-{outline.revision[:16]}",
            kind="document_outline",
            title=outline.name,
            description="选择章节即可查看对应内容，无需再次发送消息。",
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
                    "Reply exactly in Chinese: 已加载章节导航，请在下方组件中选择要查看的章节。 "
                    "Do not add headings, counts, or other explanation."
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


async def invoke_document_edit_task_tool(
    service: DocumentEditTaskService,
    tool_id: str,
    arguments: dict[str, Any],
    *,
    user_id: str,
    tenant_id: str,
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
                description=description,
                sections=sections,
            ),
        )
        return {"task": task.model_dump(mode="json")}, []

    if tool_id == LIST_EDIT_TASKS_TOOL_ID:
        name = _required_string(arguments, "name", tool_id)
        tasks = await service.list_tasks(name, user_id=user_id, tenant_id=tenant_id)
        return {"count": len(tasks), "tasks": [task.model_dump(mode="json") for task in tasks]}, []

    task_id = _required_string(arguments, "task_id", tool_id)
    if tool_id == READ_EDIT_TASK_TOOL_ID:
        task = await service.get_task(task_id, user_id=user_id, tenant_id=tenant_id)
    elif tool_id == UPDATE_DRAFT_TOOL_ID:
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
    elif tool_id == MERGE_EDIT_TASK_TOOL_ID:
        task = await service.merge_task(task_id, user_id=user_id, tenant_id=tenant_id)
    else:
        raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown document edit task tool {tool_id!r}", status_code=404)
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
