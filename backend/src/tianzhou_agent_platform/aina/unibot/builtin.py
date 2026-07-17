from typing import Any
from urllib.parse import urlencode

from tianzhou_agent_platform.aina.protocol.models import (
    AinaCanvasResponse,
    AinaCapabilities,
    AinaCapability,
    AinaIdentity,
    AinaManifest,
    AinaRecord,
    AinaUiCapability,
    BuiltinRuntimeDefinition,
    Publisher,
)
from tianzhou_agent_platform.aina.protocol.widgets import WidgetAction, WidgetApp, WidgetDefinition, WidgetField
from tianzhou_agent_platform.aina.security.models import Authentication
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository

UNIBOT_ASSISTANT_ID = "unibot-assistant"
LIST_APP_TOOL_ID = "list_app"
OPEN_AINA_TOOL_ID = "open_aina"
REQUEST_CLARIFICATION_TOOL_ID = "request_clarification"
UNIBOT_TOOL_IDS = {LIST_APP_TOOL_ID, OPEN_AINA_TOOL_ID, REQUEST_CLARIFICATION_TOOL_ID}


def unibot_assistant_record() -> AinaRecord:
    return AinaRecord(
        manifest=AinaManifest(
            protocol_version="1.0",
            aina=AinaIdentity(
                id=UNIBOT_ASSISTANT_ID,
                name="Unibot 助手",
                version="1.0.0",
                description="负责发现应用、路由请求并打开 AINA 画布的系统应用。",
                publisher=Publisher(id="unibot", name="Unibot"),
            ),
            runtime=BuiltinRuntimeDefinition(),
            capabilities=AinaCapabilities(
                tools=[
                    AinaCapability(
                        id=LIST_APP_TOOL_ID,
                        name="列出应用",
                        description="以组件形式列出当前用户可用的全部 AINA 应用。",
                    ),
                    AinaCapability(
                        id=OPEN_AINA_TOOL_ID,
                        name="打开 AINA",
                        description="打开选定的 AINA，并返回其画布路由和主组件。",
                    ),
                    AinaCapability(
                        id=REQUEST_CLARIFICATION_TOOL_ID,
                        name="请求补充信息",
                        description="缺少必要信息时显示由平台渲染的表单。",
                    ),
                ],
                ui=[
                    AinaUiCapability(
                        id="clarification-form",
                        kind="form",
                        description="由平台渲染、用于收集缺失信息的表单。",
                        instructions=(
                            "Declare fields, labels, required flags, and optional prefilled values. The host renders "
                            "and submits the form without AINA-specific frontend code."
                        ),
                    )
                ],
            ),
            main_widget=WidgetDefinition(
                id="unibot-assistant-main",
                kind="panel",
                title="Unibot 助手",
                description="发现并打开当前可用的 AINA 应用。",
            ),
            authentication=Authentication(type="none"),
        ),
        last_health={"status": "healthy", "runtime": "builtin"},
    )


async def list_app_widget(
    repository: InMemoryRepository,
    *,
    user_id: str,
    tenant_id: str,
) -> WidgetDefinition:
    apps = await _available_apps(repository, user_id=user_id, tenant_id=tenant_id)
    return WidgetDefinition(
        id="unibot-app-list",
        kind="app_list",
        title="AINA 应用",
        description=f"当前共有 {len(apps)} 个可用应用。",
        apps=apps,
    )


async def assistant_main_widget(
    repository: InMemoryRepository,
    *,
    user_id: str,
    tenant_id: str,
) -> WidgetDefinition:
    apps = await _available_apps(repository, user_id=user_id, tenant_id=tenant_id)
    return WidgetDefinition(
        id="unibot-assistant-main",
        kind="panel",
        title="Unibot 助手",
        description="发现并打开当前可用的 AINA 应用。",
        apps=apps,
    )


async def _available_apps(
    repository: InMemoryRepository,
    *,
    user_id: str,
    tenant_id: str,
) -> list[WidgetApp]:
    from tianzhou_agent_platform.aina.document.builtin import UNIBOT_DOCUMENTS_ID
    from tianzhou_agent_platform.aina.memory.builtin import UNIBOT_MEMORY_ID

    builtin_aina_ids = {UNIBOT_ASSISTANT_ID, UNIBOT_MEMORY_ID, UNIBOT_DOCUMENTS_ID}
    installations = {
        item.aina_id: item
        for item in await repository.list_installations(tenant_id=tenant_id, user_id=user_id)
        if item.status == "active"
    }
    apps: list[WidgetApp] = []
    for record in await repository.list_ainas():
        manifest = record.manifest
        aina_id = manifest.aina.id
        if record.status != "registered":
            continue
        if manifest.runtime.type != "builtin":
            installation = installations.get(aina_id)
            if installation is None:
                continue
            if set(manifest.permissions) - set(installation.granted_permissions):
                continue
        apps.append(
            WidgetApp(
                aina_id=aina_id,
                name=manifest.aina.name,
                description=manifest.aina.description,
                version=manifest.aina.version,
                publisher=manifest.aina.publisher.name,
                installed=True,
                has_main_widget=manifest.main_widget is not None,
            )
        )
    apps.sort(key=lambda item: (item.aina_id not in builtin_aina_ids, item.name.casefold()))
    return apps


async def open_aina(
    repository: InMemoryRepository,
    aina_id: str,
    *,
    user_id: str,
    tenant_id: str,
    conversation_id: str | None = None,
) -> AinaCanvasResponse:
    record = await repository.get_aina(aina_id)
    if record.status != "registered":
        raise PlatformError("PERMISSION_DENIED", "The selected AINA is disabled", status_code=403)
    if record.manifest.runtime.type != "builtin":
        installation = await repository.get_installation(
            tenant_id=tenant_id,
            user_id=user_id,
            aina_id=aina_id,
        )
        if installation.status != "active":
            raise PlatformError("PERMISSION_DENIED", "The selected AINA is not active", status_code=403)
        missing = set(record.manifest.permissions) - set(installation.granted_permissions)
        if missing:
            raise PlatformError(
                "PERMISSION_DENIED",
                f"AINA is missing grants: {', '.join(sorted(missing))}",
                status_code=403,
            )

    main_widget = record.manifest.main_widget
    if aina_id == UNIBOT_ASSISTANT_ID:
        main_widget = await assistant_main_widget(repository, user_id=user_id, tenant_id=tenant_id)
    if main_widget is None:
        main_widget = _default_main_widget(record)
    query = urlencode({"conversation": conversation_id}) if conversation_id else ""
    route = f"/canvas/{aina_id}{f'?{query}' if query else ''}"
    return AinaCanvasResponse(
        aina_id=aina_id,
        name=record.manifest.aina.name,
        description=record.manifest.aina.description,
        version=record.manifest.aina.version,
        conversation_id=conversation_id,
        route=route,
        main_widget=main_widget,
    )


async def invoke_unibot_tool(
    repository: InMemoryRepository,
    tool_id: str,
    arguments: dict[str, Any],
    *,
    user_id: str,
    tenant_id: str,
    conversation_id: str,
) -> tuple[dict[str, Any], list[WidgetDefinition]]:
    if tool_id == LIST_APP_TOOL_ID:
        widget = await list_app_widget(repository, user_id=user_id, tenant_id=tenant_id)
        return {"count": len(widget.apps), "aina_ids": [item.aina_id for item in widget.apps]}, [widget]
    if tool_id == OPEN_AINA_TOOL_ID:
        aina_id = str(arguments.get("aina_id") or "").strip()
        if not aina_id:
            raise PlatformError("INVALID_REQUEST", "open_aina requires aina_id")
        canvas = await open_aina(
            repository,
            aina_id,
            user_id=user_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
        )
        widget = WidgetDefinition(
            id=f"open-{aina_id}",
            kind="navigation",
            title=f"打开 {canvas.name}",
            description="该应用已准备好，可以进入 Canvas。",
            actions=[
                WidgetAction(
                    id="open",
                    label="进入 Canvas",
                    kind="open_aina",
                    aina_id=aina_id,
                )
            ],
        )
        return canvas.model_dump(mode="json"), [widget]
    if tool_id == REQUEST_CLARIFICATION_TOOL_ID:
        raw_fields = arguments.get("fields")
        if not isinstance(raw_fields, list) or not raw_fields or len(raw_fields) > 6:
            raise PlatformError("INVALID_REQUEST", "request_clarification requires between 1 and 6 fields")
        fields: list[WidgetField] = []
        field_ids: set[str] = set()
        try:
            for index, raw_field in enumerate(raw_fields):
                if not isinstance(raw_field, dict):
                    raise ValueError("Each clarification field must be an object")
                field_id = str(raw_field.get("id") or f"field_{index + 1}").strip()
                if field_id in field_ids:
                    raise ValueError(f"Duplicate clarification field id: {field_id}")
                field_ids.add(field_id)
                fields.append(
                    WidgetField.model_validate(
                        {
                            "id": field_id,
                            "label": str(raw_field.get("label") or field_id).strip(),
                            "input_type": str(raw_field.get("input_type") or "text"),
                            "placeholder": str(raw_field.get("placeholder") or ""),
                            "required": bool(raw_field.get("required", True)),
                            "value": str(raw_field["value"]) if raw_field.get("value") is not None else None,
                        }
                    )
                )
        except ValueError as exc:
            raise PlatformError("INVALID_REQUEST", f"Invalid clarification form: {exc}") from exc
        answer_lines = "\n".join(f"{field.label}: {{{field.id}}}" for field in fields)
        widget = WidgetDefinition(
            id=f"clarification-{conversation_id}",
            kind="form",
            title=str(arguments.get("title") or "补充信息"),
            description=str(arguments.get("description") or "请补充以下信息，以便继续处理。"),
            fields=fields,
            actions=[
                WidgetAction(
                    id="submit-clarification",
                    label=str(arguments.get("submit_label") or "提交并继续"),
                    kind="prompt",
                    prompt=f"以下是我的补充信息：\n{answer_lines}",
                )
            ],
        )
        return {"requested": True, "field_ids": [field.id for field in fields]}, [widget]
    raise PlatformError("RESOURCE_NOT_FOUND", f"Unknown Unibot tool {tool_id!r}", status_code=404)


def _default_main_widget(record: AinaRecord) -> WidgetDefinition:
    manifest = record.manifest
    return WidgetDefinition(
        id=f"{manifest.aina.id}-main",
        kind="form",
        title=manifest.aina.name,
        description=manifest.aina.description,
        markdown="### 描述你的需求\n\n提交后，消息会直接路由到当前 AINA。",
        fields=[
            WidgetField(
                id="request",
                label="需求",
                input_type="textarea",
                placeholder="请描述希望这个应用完成的任务",
                required=True,
            )
        ],
        actions=[
            WidgetAction(
                id="submit",
                label="交给 AINA",
                kind="prompt",
                prompt="{request}",
            )
        ],
    )
