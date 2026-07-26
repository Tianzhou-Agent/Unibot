from fastapi import APIRouter, Query, Request, Response, status

from tianzhou_agent_platform.aina.builtin import BUILTIN_AINA_IDS
from tianzhou_agent_platform.aina.protocol.models import (
    AinaCanvasResponse,
    AinaInstallation,
    AinaManifest,
    AinaRecord,
    InstallationRequest,
    OpenAinaRequest,
    PermissionUpdate,
)
from tianzhou_agent_platform.aina.skill.models import SkillCreate, SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolCreate, ToolRecord
from tianzhou_agent_platform.api.dependencies import gateway, repository
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.builtin_tools import open_aina
from tianzhou_agent_platform.core.schema import validate_schema


def create_capability_router() -> APIRouter:
    router = APIRouter()

    @router.post("/tools", response_model=ToolRecord, status_code=status.HTTP_201_CREATED)
    async def register_tool(payload: ToolCreate, request: Request) -> ToolRecord:
        validate_schema(payload.input_schema, label="input_schema")
        if payload.output_schema:
            validate_schema(payload.output_schema, label="output_schema")
        values = {name: getattr(payload, name) for name in ToolCreate.model_fields}
        return await repository(request).register_tool(ToolRecord(**values))

    @router.get("/tools", response_model=list[ToolRecord])
    async def list_tools(request: Request) -> list[ToolRecord]:
        return await repository(request).list_tools()

    @router.get("/tools/{tool_id}", response_model=ToolRecord)
    async def get_tool(tool_id: str, request: Request) -> ToolRecord:
        return await repository(request).get_tool(tool_id)

    @router.delete("/tools/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_tool(tool_id: str, request: Request) -> Response:
        await repository(request).remove_tool(tool_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/skills", response_model=SkillRecord, status_code=status.HTTP_201_CREATED)
    async def register_skill(payload: SkillCreate, request: Request) -> SkillRecord:
        validate_schema(payload.input_schema, label="input_schema")
        validate_schema(payload.output_schema, label="output_schema")
        data_repository = repository(request)
        for tool_id in payload.tools:
            await data_repository.get_tool(tool_id)
        return await data_repository.register_skill(SkillRecord(**payload.model_dump()))

    @router.get("/skills", response_model=list[SkillRecord])
    async def list_skills(request: Request) -> list[SkillRecord]:
        return await repository(request).list_skills()

    @router.get("/skills/{skill_id}", response_model=SkillRecord)
    async def get_skill(skill_id: str, request: Request) -> SkillRecord:
        return await repository(request).get_skill(skill_id)

    @router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_skill(skill_id: str, request: Request) -> Response:
        await repository(request).remove_skill(skill_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/ainas", response_model=AinaRecord, status_code=status.HTTP_201_CREATED)
    async def register_aina(payload: AinaManifest, request: Request) -> AinaRecord:
        if payload.runtime.type != "remote":
            raise PlatformError(
                "PERMISSION_DENIED",
                "Built-in AINA runtimes can only be registered by the platform",
                status_code=403,
            )
        for capability in [*payload.capabilities.skills, *payload.capabilities.tools]:
            validate_schema(capability.input_schema, label=f"capability {capability.id} input_schema")
        health = await gateway(request).probe_aina(payload)
        return await repository(request).register_aina(AinaRecord(manifest=payload, last_health=health))

    @router.get("/ainas", response_model=list[AinaRecord])
    async def list_ainas(request: Request) -> list[AinaRecord]:
        return await repository(request).list_ainas()

    @router.get("/ainas/{aina_id}", response_model=AinaRecord)
    async def get_aina(aina_id: str, request: Request) -> AinaRecord:
        return await repository(request).get_aina(aina_id)

    @router.post("/ainas/{aina_id}/open", response_model=AinaCanvasResponse)
    async def open_aina_canvas(
        aina_id: str,
        payload: OpenAinaRequest,
        request: Request,
    ) -> AinaCanvasResponse:
        return await open_aina(
            repository(request),
            aina_id,
            user_id=payload.user_id,
            tenant_id=payload.tenant_id,
            conversation_id=payload.conversation_id,
        )

    @router.delete("/ainas/{aina_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_aina(aina_id: str, request: Request) -> Response:
        if aina_id in BUILTIN_AINA_IDS:
            raise PlatformError("PERMISSION_DENIED", "The system AINA cannot be deleted", status_code=403)
        await repository(request).remove_aina(aina_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/ainas/{aina_id}/install", response_model=AinaInstallation)
    async def install_aina(
        aina_id: str,
        payload: InstallationRequest,
        request: Request,
    ) -> AinaInstallation:
        if aina_id in BUILTIN_AINA_IDS:
            raise PlatformError("CONFLICT", "The system AINA is always available", status_code=409)
        data_repository = repository(request)
        record = await data_repository.get_aina(aina_id)
        _validate_permission_subset(record, payload.granted_permissions)
        installation = AinaInstallation(
            **payload.model_dump(),
            aina_id=aina_id,
            installed_version=record.manifest.aina.version,
        )
        return await data_repository.put_installation(installation)

    @router.patch("/ainas/{aina_id}/installation", response_model=AinaInstallation)
    async def update_aina_permissions(
        aina_id: str,
        payload: PermissionUpdate,
        request: Request,
    ) -> AinaInstallation:
        data_repository = repository(request)
        record = await data_repository.get_aina(aina_id)
        _validate_permission_subset(record, payload.granted_permissions)
        installation = await data_repository.get_installation(
            tenant_id=payload.tenant_id,
            user_id=payload.user_id,
            aina_id=aina_id,
        )
        updated = installation.model_copy(update={"granted_permissions": payload.granted_permissions}, deep=True)
        return await data_repository.put_installation(updated)

    @router.delete("/ainas/{aina_id}/install", status_code=status.HTTP_204_NO_CONTENT)
    async def uninstall_aina(
        aina_id: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
    ) -> Response:
        await repository(request).remove_installation(
            tenant_id=tenant_id,
            user_id=user_id,
            aina_id=aina_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/installations", response_model=list[AinaInstallation])
    async def list_installations(
        request: Request,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[AinaInstallation]:
        return await repository(request).list_installations(user_id=user_id, tenant_id=tenant_id)

    return router


def _validate_permission_subset(record: AinaRecord, granted_permissions: list[str]) -> None:
    undeclared = set(granted_permissions) - set(record.manifest.permissions)
    if undeclared:
        raise PlatformError(
            "INVALID_REQUEST",
            f"Cannot grant undeclared permissions: {', '.join(sorted(undeclared))}",
            status_code=422,
        )
