from fastapi import APIRouter, File, UploadFile, status
from fastapi.responses import Response

from tianzhou_agent_platform.aina.project import (
    MAX_PROJECT_ARCHIVE_BYTES,
    AinaProjectScaffoldRequest,
    AinaProjectValidationReport,
    scaffold_project_archive,
    validate_project_archive,
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
        payload = await file.read(MAX_PROJECT_ARCHIVE_BYTES + 1)
        await file.close()
        if len(payload) > MAX_PROJECT_ARCHIVE_BYTES:
            raise PlatformError(
                "INVALID_REQUEST",
                "AINA project archive is too large",
                status_code=422,
                source="aina_project",
            )
        return validate_project_archive(payload)

    return router
