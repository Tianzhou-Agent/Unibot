"""Routes behind the Add app page: build a package from its parts, list an MCP server's tools, check a canvas address.

Building and checking need only a signed-in user, so a developer who is not an admin can prepare a package and hand
it to one. Registering needs a platform admin, as an uploaded package does. Every server and canvas address passes
the same destination allowlists as registration.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Query, Request, Response
from starlette.datastructures import UploadFile
from pydantic import SecretStr

from tianzhou_agent_platform.aina.package.compose import compose_package
from tianzhou_agent_platform.aina.package.manifest import McpAuth, McpServer
from tianzhou_agent_platform.aina.package.service import resolve_effect
from tianzhou_agent_platform.aina.package.validation import PackageValidationReport, validate_package_archive
from tianzhou_agent_platform.aina.project import MAX_PROJECT_ARCHIVE_BYTES, build_project_archive
from tianzhou_agent_platform.aina.protocol.models import AinaRecord
from tianzhou_agent_platform.api.dependencies import aina_packages, require_platform_admin
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.errors import PlatformError

SKILL_FIELD = "skill:"
_FRAME_CHECK_SECONDS = 5.0


class McpInspectRequest(StrictModel):
    url: str
    auth: McpAuth = McpAuth(type="none")
    # Used once to list the tools of a server that needs it; never stored.
    credential: SecretStr | None = None


class InspectedTool(StrictModel):
    name: str
    title: str | None = None
    description: str
    # The suggested effect: what the server's annotations imply, or ``write`` without them.
    effect: Literal["read", "write", "destructive"]
    # The loosest effect the annotations allow (the host applies the stricter of the two anyway).
    min_effect: Literal["read", "write", "destructive"]


class McpInspection(StrictModel):
    name: str | None = None
    version: str | None = None
    tools: list[InspectedTool]


class CanvasInspectRequest(StrictModel):
    url: str


class CanvasInspection(StrictModel):
    allowed: bool
    # ``None`` when the page could not be fetched to tell.
    embeddable: bool | None = None
    detail: str | None = None


class ComposeCheck(StrictModel):
    report: PackageValidationReport
    # Where an uploaded canvas bundle can be previewed (relative to the API root).
    canvas_preview: str | None = None


def create_aina_add_app_router() -> APIRouter:
    router = APIRouter(tags=["aina-add-app"])

    @router.post("/ainas/mcp/inspect", response_model=McpInspection)
    async def inspect_mcp_server(payload: McpInspectRequest, request: Request) -> McpInspection:
        server = McpServer(url=payload.url, auth=payload.auth)
        credential = payload.credential.get_secret_value() if payload.credential is not None else None
        info = await aina_packages(request).inspect_server(server, credential=credential)
        return McpInspection(
            name=info.name,
            version=info.version,
            tools=[
                InspectedTool(
                    name=tool.name,
                    title=tool.title,
                    description=tool.description or tool.title or "",
                    effect=resolve_effect(None, tool.annotations),
                    min_effect=resolve_effect("read", tool.annotations),
                )
                for tool in info.tools
            ],
        )

    @router.post("/ainas/canvas/inspect", response_model=CanvasInspection)
    async def inspect_canvas(payload: CanvasInspectRequest, request: Request) -> CanvasInspection:
        if not aina_packages(request).canvas_origin_allowed(payload.url):
            return CanvasInspection(allowed=False, detail="The address is not on the canvas allowlist")
        try:
            async with httpx.AsyncClient(timeout=_FRAME_CHECK_SECONDS, follow_redirects=True) as client:
                response = await client.get(payload.url)
        except httpx.HTTPError as exc:
            return CanvasInspection(allowed=True, detail=f"Could not load the page ({type(exc).__name__})")
        return CanvasInspection(allowed=True, **_embedding(response))

    @router.post("/ainas/packages/compose", response_model=None)
    async def compose_aina_package(
        request: Request,
        response: Response,
        mode: Literal["check", "download", "register"] = Query(default="check"),
    ) -> ComposeCheck | AinaRecord | Response:
        """``multipart/form-data``: ``manifest`` (aina.json), optional ``base`` (.aina.zip), ``skill:<id>`` files
        (SKILL.md or a folder .zip), optional ``canvas_bundle`` (.zip) and, to register, ``credential``."""
        if mode == "register":
            require_platform_admin(request)
        form = await request.form()
        try:
            manifest = json.loads(str(form.get("manifest") or ""))
        except ValueError as exc:
            raise _invalid("manifest must be the app's aina.json") from exc
        if not isinstance(manifest, dict):
            raise _invalid("manifest must be a JSON object")
        skills = {
            key[len(SKILL_FIELD) :]: (upload.filename or "SKILL.md", await _read(upload))
            for key, upload in form.multi_items()
            if key.startswith(SKILL_FIELD) and isinstance(upload, UploadFile)
        }
        base, bundle = form.get("base"), form.get("canvas_bundle")
        files = compose_package(
            manifest,
            base=await _read(base) if isinstance(base, UploadFile) else None,
            skills=skills,
            canvas_bundle=await _read(bundle) if isinstance(bundle, UploadFile) else None,
        )
        archive = build_project_archive(files)
        if mode == "download":
            aina_id = str((manifest.get("aina") or {}).get("id") or "app")
            return Response(
                archive,
                media_type="application/zip",
                headers={"Content-Disposition": f'attachment; filename="{aina_id}.aina.zip"'},
            )
        if mode == "register":
            credential = form.get("credential")
            response.status_code = 201
            return await aina_packages(request).register(
                archive, credential=credential if isinstance(credential, str) and credential else None
            )
        report = validate_package_archive(archive)
        preview = None
        canvas = report.manifest.canvas if report.manifest is not None else None
        packages = aina_packages(request)
        if canvas is not None and canvas.entry is not None and packages.ui is not None and report.archive_sha256:
            packages.ui.save_draft(report.archive_sha256, files, [skill.path for skill in report.manifest.skills])  # type: ignore[union-attr]
            preview = f"/aina-ui/drafts/{report.archive_sha256}/{canvas.entry}"
        return ComposeCheck(report=report, canvas_preview=preview)

    return router


async def _read(upload: UploadFile) -> bytes:
    try:
        payload = await upload.read(MAX_PROJECT_ARCHIVE_BYTES + 1)
    finally:
        await upload.close()
    if len(payload) > MAX_PROJECT_ARCHIVE_BYTES:
        raise _invalid(
            f"{upload.filename or 'An upload'} is larger than {MAX_PROJECT_ARCHIVE_BYTES // (1024 * 1024)} MiB"
        )
    return payload


def _embedding(response: httpx.Response) -> dict[str, Any]:
    """Whether the page lets another site frame it (``X-Frame-Options`` and CSP ``frame-ancestors``)."""
    frame_options = response.headers.get("x-frame-options", "").strip().lower()
    if frame_options in {"deny", "sameorigin"}:
        return {"embeddable": False, "detail": f"The page sends X-Frame-Options: {frame_options.upper()}"}
    for directive in response.headers.get("content-security-policy", "").split(";"):
        name, _, value = directive.strip().partition(" ")
        if name.lower() == "frame-ancestors" and value.strip() in {"'none'", "'self'"}:
            return {"embeddable": False, "detail": f"The page's CSP sets frame-ancestors {value.strip()}"}
    if response.status_code >= 400:
        return {"embeddable": None, "detail": f"The page answered {response.status_code}"}
    return {"embeddable": True}


def _invalid(message: str) -> PlatformError:
    return PlatformError("INVALID_REQUEST", message, status_code=422, source="aina_package")
