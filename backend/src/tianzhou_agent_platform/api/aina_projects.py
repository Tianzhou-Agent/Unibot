import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status

from tianzhou_agent_platform.aina.project import (
    MAX_PROJECT_ARCHIVE_BYTES,
    AinaProjectRecord,
    AinaProjectScaffoldRequest,
    AinaProjectValidationReport,
    scaffold_project_archive,
    validate_project_archive,
)
from tianzhou_agent_platform.api.dependencies import (
    RequestActor,
    aina_projects,
    managed_ainas,
    request_actor,
)
from tianzhou_agent_platform.core.errors import PlatformError


def create_aina_project_router() -> APIRouter:
    router = APIRouter(prefix="/aina-projects", tags=["aina-projects"])

    @router.post("/scaffold", status_code=status.HTTP_200_OK)
    async def scaffold_aina_project(payload: AinaProjectScaffoldRequest) -> Response:
        archive = scaffold_project_archive(payload)
        return Response(
            archive,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{payload.aina_id}-{payload.version}.aina.zip"',
            },
        )

    @router.post("/validate", response_model=AinaProjectValidationReport)
    async def validate_aina_project(file: UploadFile = File(...)) -> AinaProjectValidationReport:
        payload = await _read_project_upload(file)
        return validate_project_archive(payload)

    @router.post("", response_model=AinaProjectRecord, status_code=status.HTTP_201_CREATED)
    async def import_aina_project(
        request: Request,
        file: UploadFile = File(...),
        actor: RequestActor = Depends(request_actor),
    ) -> AinaProjectRecord:
        payload = await _read_project_upload(file)
        return await aina_projects(request).import_project(
            payload,
            source_filename=file.filename,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )

    @router.get("", response_model=list[AinaProjectRecord])
    async def list_aina_projects(
        request: Request,
        actor: RequestActor = Depends(request_actor),
    ) -> list[AinaProjectRecord]:
        return await aina_projects(request).list_projects(
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )

    @router.get("/{project_id}/archive")
    async def download_aina_project(
        project_id: str,
        request: Request,
        actor: RequestActor = Depends(request_actor),
    ) -> Response:
        record, payload = await aina_projects(request).get_archive(
            project_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        return Response(
            payload,
            media_type="application/zip",
            headers={"Content-Disposition": _content_disposition(record.source_filename)},
        )

    @router.post("/{project_id}/deploy", response_model=AinaProjectRecord)
    async def deploy_aina_project(
        project_id: str,
        request: Request,
        actor: RequestActor = Depends(request_actor),
    ) -> AinaProjectRecord:
        return await managed_ainas(request).deploy(
            project_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )

    @router.delete("/{project_id}/deployment", response_model=AinaProjectRecord)
    async def undeploy_aina_project(
        project_id: str,
        request: Request,
        actor: RequestActor = Depends(request_actor),
    ) -> AinaProjectRecord:
        return await managed_ainas(request).undeploy(
            project_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )

    @router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
    async def delete_aina_project(
        project_id: str,
        request: Request,
        actor: RequestActor = Depends(request_actor),
    ) -> Response:
        await aina_projects(request).delete_project(
            project_id,
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return router


async def _read_project_upload(file: UploadFile) -> bytes:
    try:
        payload = await file.read(MAX_PROJECT_ARCHIVE_BYTES + 1)
    finally:
        await file.close()
    if len(payload) > MAX_PROJECT_ARCHIVE_BYTES:
        raise PlatformError(
            "INVALID_REQUEST",
            "AINA project archive is too large",
            status_code=422,
            source="aina_project",
        )
    return payload


def _content_disposition(filename: str) -> str:
    fallback = re.sub(r"[^A-Za-z0-9._-]", "_", filename) or "aina-project.zip"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
