from fastapi import APIRouter, Body, File, Form, Query, Request, Response, UploadFile, status
from pydantic import SecretStr

from tianzhou_agent_platform.aina.builtin import BUILTIN_AINA_IDS
from tianzhou_agent_platform.aina.package.bundled import is_bundled
from tianzhou_agent_platform.aina.package.scaffold import PackageScaffoldRequest, scaffold_archive
from tianzhou_agent_platform.aina.package.validation import PackageValidationReport, validate_package_archive
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
from tianzhou_agent_platform.aina.security.access import capability_visible
from tianzhou_agent_platform.aina.tool.models import ToolCreate, ToolRecord
from tianzhou_agent_platform.api.aina_projects import read_archive_upload
from tianzhou_agent_platform.api.dependencies import (
    actor_scope, aina_packages, bind_actor, gateway, repository, request_actor, require_platform_admin, settings,
)
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.memory_repository import installation_record_id
from tianzhou_agent_platform.services.platform_tools import open_aina
from tianzhou_agent_platform.core.schema import validate_schema


def create_capability_router() -> APIRouter:
    router = APIRouter()

    @router.post("/tools", response_model=ToolRecord, status_code=status.HTTP_201_CREATED)
    async def register_tool(payload: ToolCreate, request: Request) -> ToolRecord:
        require_platform_admin(request)
        gateway(request).require_approved_destination(str(payload.endpoint))
        validate_schema(payload.input_schema, label="input_schema")
        if payload.output_schema:
            validate_schema(payload.output_schema, label="output_schema")
        values = {name: getattr(payload, name) for name in ToolCreate.model_fields}
        actor = request_actor(request)
        return await repository(request).register_tool(
            ToolRecord(**values, owner_user_id=actor.user_id, tenant_id=actor.tenant_id)
        )

    @router.get("/tools", response_model=list[ToolRecord])
    async def list_tools(request: Request) -> list[ToolRecord]:
        return [record for record in await repository(request).list_tools() if _visible(request, record)]

    @router.get("/tools/{tool_id}", response_model=ToolRecord)
    async def get_tool(tool_id: str, request: Request) -> ToolRecord:
        record = await repository(request).get_tool(tool_id)
        _require_visible(request, record)
        return record

    @router.delete("/tools/{tool_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_tool(tool_id: str, request: Request) -> Response:
        require_platform_admin(request)
        await repository(request).remove_tool(tool_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/skills", response_model=SkillRecord, status_code=status.HTTP_201_CREATED)
    async def register_skill(payload: SkillCreate, request: Request) -> SkillRecord:
        require_platform_admin(request)
        validate_schema(payload.input_schema, label="input_schema")
        validate_schema(payload.output_schema, label="output_schema")
        data_repository = repository(request)
        for tool_id in payload.tools:
            await data_repository.get_tool(tool_id)
        actor = request_actor(request)
        return await data_repository.register_skill(
            SkillRecord(**payload.model_dump(), owner_user_id=actor.user_id, tenant_id=actor.tenant_id)
        )

    @router.get("/skills", response_model=list[SkillRecord])
    async def list_skills(request: Request) -> list[SkillRecord]:
        return [record for record in await repository(request).list_skills() if _visible(request, record)]

    @router.get("/skills/{skill_id}", response_model=SkillRecord)
    async def get_skill(skill_id: str, request: Request) -> SkillRecord:
        record = await repository(request).get_skill(skill_id)
        _require_visible(request, record)
        return record

    @router.delete("/skills/{skill_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_skill(skill_id: str, request: Request) -> Response:
        require_platform_admin(request)
        await repository(request).remove_skill(skill_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/ainas", response_model=AinaRecord, status_code=status.HTTP_201_CREATED)
    async def register_aina(payload: AinaManifest, request: Request) -> AinaRecord:
        require_platform_admin(request)
        if payload.runtime.type != "remote":
            raise PlatformError(
                "PERMISSION_DENIED",
                "Only deployed remote AINA runtimes can be registered directly",
                status_code=403,
            )
        for capability in [*payload.capabilities.skills, *payload.capabilities.tools]:
            validate_schema(capability.input_schema, label=f"capability {capability.id} input_schema")
        health = await gateway(request).probe_aina(payload)
        return await repository(request).register_aina(AinaRecord(manifest=payload, last_health=health))

    @router.post("/ainas/packages/scaffold")
    async def scaffold_aina_package(payload: PackageScaffoldRequest) -> Response:
        """A new AINA 2.0 project as a zip (``package/`` and its MCP ``server/``), like ``aina init``."""
        return Response(
            scaffold_archive(payload),
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{payload.aina_id}.zip"'},
        )

    @router.post("/ainas/packages/validate", response_model=PackageValidationReport)
    async def validate_aina_package(file: UploadFile = File(...)) -> PackageValidationReport:
        """AINA Protocol 2.0 package checks that need no MCP server (the CLI adds the live tools/list check)."""
        return validate_package_archive(await read_archive_upload(file))

    @router.post("/ainas/packages", response_model=AinaRecord, status_code=status.HTTP_201_CREATED)
    async def register_aina_package(
        request: Request,
        file: UploadFile = File(...),
        credential: SecretStr | None = Form(default=None),
    ) -> AinaRecord:
        """``credential`` authenticates the registration-time tools/list of a bearer/api_key server; not stored."""
        require_platform_admin(request)
        return await aina_packages(request).register(
            await read_archive_upload(file),
            credential=credential.get_secret_value() if credential is not None else None,
        )

    @router.post("/ainas/{aina_id}/tools/refresh", response_model=AinaRecord)
    async def refresh_aina_package_tools(
        aina_id: str,
        request: Request,
        credential: SecretStr | None = Body(default=None, embed=True),
    ) -> AinaRecord:
        """Re-check a package's MCP tools against its reviewed snapshot; drifted tools are disabled."""
        require_platform_admin(request)
        return await aina_packages(request).refresh_tools(
            aina_id,
            credential=credential.get_secret_value() if credential is not None else None,
        )

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
        scoped = bind_actor(request, payload)
        data_repository = repository(request)
        if scoped.conversation_id is not None:
            conversation = await data_repository.require_conversation_actor(
                scoped.conversation_id,
                user_id=scoped.user_id,
                tenant_id=scoped.tenant_id,
            )
            if scoped.workspace_id is not None and scoped.workspace_id != conversation.workspace_id:
                raise PlatformError(
                    "CONFLICT",
                    "Requested workspace does not match the conversation",
                    status_code=409,
                )
        elif scoped.workspace_id is not None:
            await data_repository.require_workspace_actor(
                scoped.workspace_id,
                user_id=scoped.user_id,
                tenant_id=scoped.tenant_id,
            )
        return await open_aina(
            data_repository,
            aina_id,
            user_id=scoped.user_id,
            tenant_id=scoped.tenant_id,
            conversation_id=scoped.conversation_id,
        )

    @router.delete("/ainas/{aina_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_aina(aina_id: str, request: Request) -> Response:
        require_platform_admin(request)
        data_repository = repository(request)
        record = await data_repository.get_aina(aina_id)
        if aina_id in BUILTIN_AINA_IDS or is_bundled(record):
            raise PlatformError("PERMISSION_DENIED", "The system AINA cannot be deleted", status_code=403)
        if record.manifest.runtime.type == "managed":
            raise PlatformError(
                "CONFLICT",
                "Undeploy managed AINA from its project before deleting it",
                status_code=409,
            )
        await data_repository.remove_aina(aina_id)
        await data_repository.delete_aina_storage(aina_id=aina_id)
        await data_repository.delete_aina_notifications(aina_id=aina_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.post("/ainas/{aina_id}/install", response_model=AinaInstallation)
    async def install_aina(
        aina_id: str,
        payload: InstallationRequest,
        request: Request,
    ) -> AinaInstallation:
        scoped = bind_actor(request, payload)
        data_repository = repository(request)
        record = await data_repository.get_aina(aina_id)
        if aina_id in BUILTIN_AINA_IDS or is_bundled(record):
            raise PlatformError("CONFLICT", "The system AINA is always available", status_code=409)
        if record.manifest.runtime.type == "package":
            return await aina_packages(request).install(aina_id, scoped)
        _validate_permission_subset(record, scoped.granted_permissions)
        installation = AinaInstallation(
            **scoped.model_dump(),
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
        scoped = bind_actor(request, payload)
        data_repository = repository(request)
        record = await data_repository.get_aina(aina_id)
        _validate_permission_subset(record, scoped.granted_permissions)
        installation = await data_repository.get_installation(
            tenant_id=scoped.tenant_id,
            user_id=scoped.user_id,
            aina_id=aina_id,
        )
        updated = installation.model_copy(update={"granted_permissions": scoped.granted_permissions}, deep=True)
        return await data_repository.put_installation(updated)

    @router.delete("/ainas/{aina_id}/install", status_code=status.HTTP_204_NO_CONTENT)
    async def uninstall_aina(
        aina_id: str,
        request: Request,
        user_id: str = Query(default="anonymous"),
        tenant_id: str = Query(default="default"),
        keep_data: bool = Query(default=False, description="Keep the AINA's private storage for a reinstall."),
    ) -> Response:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        data_repository = repository(request)
        await data_repository.remove_installation(
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            aina_id=aina_id,
        )
        await data_repository.delete_aina_notifications(
            aina_id=aina_id, tenant_id=actor.tenant_id, user_id=actor.user_id
        )
        if not keep_data:
            await data_repository.delete_aina_storage(
                installation_key=installation_record_id(actor.tenant_id, actor.user_id, aina_id)
            )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @router.get("/installations", response_model=list[AinaInstallation])
    async def list_installations(
        request: Request,
        user_id: str | None = None,
        tenant_id: str | None = None,
    ) -> list[AinaInstallation]:
        actor = actor_scope(request, user_id=user_id, tenant_id=tenant_id)
        return await repository(request).list_installations(user_id=actor.user_id, tenant_id=actor.tenant_id)

    return router


def _visible(request: Request, record: ToolRecord | SkillRecord) -> bool:
    actor = request_actor(request)
    return capability_visible(
        record,
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        auth_enforced=bool(request.app.state.auth_enforced),
        is_admin=settings(request).is_platform_admin(user_id=actor.user_id),
    )


def _require_visible(request: Request, record: ToolRecord | SkillRecord) -> None:
    if not _visible(request, record):
        raise PlatformError("RESOURCE_NOT_FOUND", "Capability was not found", status_code=404)


def _validate_permission_subset(record: AinaRecord, granted_permissions: list[str]) -> None:
    undeclared = set(granted_permissions) - set(record.manifest.permissions)
    if undeclared:
        raise PlatformError(
            "INVALID_REQUEST",
            f"Cannot grant undeclared permissions: {', '.join(sorted(undeclared))}",
            status_code=422,
        )
