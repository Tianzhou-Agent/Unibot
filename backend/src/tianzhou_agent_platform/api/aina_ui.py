"""Routes behind the canvas and widgets of AINA 2.0 packages (design §9).

``/aina-ui/...`` serves packaged UI files to sandboxed iframes and needs no session. The other routes are called by
the host shell for the signed-in user, never by the iframe: the UI bridge relays an iframe's request only after the
host has applied its own checks and confirmations.
"""

from __future__ import annotations

from importlib import resources
from typing import Any, Literal

from fastapi import APIRouter, File, Form, Request, Response, UploadFile, status

from tianzhou_agent_platform.aina.package.service import package_runtime
from tianzhou_agent_platform.aina.package.ui import origin_of, ui_headers
from tianzhou_agent_platform.api.conversations import read_upload
from tianzhou_agent_platform.api.dependencies import (
    aina_packages,
    chat_attachments,
    conversation_service,
    repository,
    request_actor,
    require_actor_ownership,
    settings,
)
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.errors import not_found

AINA_UI_PREFIX = "/aina-ui"
_SDK_FILES = {"aina-ui.js": "text/javascript; charset=utf-8", "aina-ui.css": "text/css; charset=utf-8"}


class AppToolCall(StrictModel):
    arguments: dict[str, Any]
    source: Literal["canvas", "widget"] = "canvas"
    # The conversation the canvas or widget is open beside, so the AINA can keep data per conversation.
    conversation_id: str | None = None
    # Set by the shell after it asked the user; the AINA's UI cannot set it.
    confirmed: bool = False


class IdentityToken(StrictModel):
    token: str


class SavedFile(StrictModel):
    # The file's name in the user's file tree, e.g. "Uploads/Deck.pptx".
    name: str


def create_aina_ui_router() -> APIRouter:
    router = APIRouter(tags=["aina-ui"])

    @router.get(f"{AINA_UI_PREFIX}/sdk/{{name}}")
    async def aina_ui_sdk(name: str) -> Response:
        """The ``@unibot/aina-ui`` bridge (the copy ``aina init`` vendors), for a canvas or widget to load or vendor."""
        media = _SDK_FILES.get(name)
        if media is None:
            raise not_found("AINA UI SDK file", name)
        content = (
            resources.files("tianzhou_agent_platform.aina.package")
            .joinpath(f"template/package/canvas/{name}")
            .read_bytes()
        )
        # Any canvas origin may import the module; it holds no data, only the bridge code.
        return Response(
            content,
            media_type=media,
            headers={"Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=3600"},
        )

    @router.get(f"{AINA_UI_PREFIX}/drafts/{{archive_sha256}}/{{path:path}}")
    async def aina_ui_draft_file(archive_sha256: str, path: str, request: Request) -> Response:
        """A UI file of a package checked on the Add app page but not registered, for the canvas preview."""
        packages = aina_packages(request)
        if packages.ui is None:
            raise not_found("AINA UI file", path)
        content, media_type = packages.ui.draft_file(archive_sha256, path)
        ancestors = [origin_of(settings(request).frontend_base_url)]
        return Response(content, media_type=media_type, headers=ui_headers(frame_ancestors=ancestors))

    @router.get(f"{AINA_UI_PREFIX}/{{aina_id}}/{{version}}/{{path:path}}")
    async def aina_ui_file(aina_id: str, version: str, path: str, request: Request) -> Response:
        """One UI file of the registered version of a package, served for a sandboxed frame."""
        record = await repository(request).get_aina(aina_id)
        if record.manifest.runtime.type != "package" or record.manifest.aina.version != version:
            raise not_found("AINA UI file", path)
        packages = aina_packages(request)
        if packages.ui is None:
            raise not_found("AINA UI file", path)
        content, media_type = await packages.ui.file(aina_id, package_runtime(record), path)
        ancestors = [origin_of(settings(request).frontend_base_url)]
        return Response(content, media_type=media_type, headers=ui_headers(frame_ancestors=ancestors))

    @router.post("/ainas/{aina_id}/app-tools/{tool_name}")
    async def call_aina_app_tool(
        aina_id: str, tool_name: str, payload: AppToolCall, request: Request
    ) -> dict[str, Any]:
        """A tool call relayed from the AINA's canvas or widget (design §9.4 ``callTool``)."""
        actor = request_actor(request)
        if payload.conversation_id is not None:
            conversation = await conversation_service(request).get(payload.conversation_id)
            require_actor_ownership(request, user_id=conversation.user_id, tenant_id=conversation.tenant_id)
        return await aina_packages(request).call_app_tool(
            aina_id,
            tool_name,
            payload.arguments,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            source=payload.source,
            confirmed=payload.confirmed,
            conversation_id=payload.conversation_id,
        )

    @router.post("/ainas/{aina_id}/files", response_model=SavedFile, status_code=status.HTTP_201_CREATED)
    async def save_aina_file(
        aina_id: str,
        request: Request,
        file: UploadFile = File(...),
        conversation_id: str | None = Form(None),
    ) -> SavedFile:
        """``saveFile``: a file the AINA's canvas or widget made, kept in the user's Files (design §9.4).

        The shell asks the user first. Beside a conversation of a workspace, the file goes to the workspace's Files.
        """
        actor = request_actor(request)
        await aina_packages(request).require_usable(aina_id, tenant_id=actor.tenant_id, user_id=actor.user_id)
        workspace_id = None
        if conversation_id is not None:
            conversation = await conversation_service(request).get(conversation_id)
            require_actor_ownership(request, user_id=conversation.user_id, tenant_id=conversation.tenant_id)
            workspace_id = conversation.workspace_id
        payload = await read_upload(file)
        name = await chat_attachments(request).save_file(
            file.filename, payload, tenant_id=actor.tenant_id, user_id=actor.user_id, workspace_id=workspace_id
        )
        return SavedFile(name=name)

    @router.post("/ainas/{aina_id}/identity-token", response_model=IdentityToken)
    async def aina_identity_token(aina_id: str, request: Request) -> IdentityToken:
        """``getIdentityToken()`` of a URL canvas (design §9.1)."""
        actor = request_actor(request)
        token = await aina_packages(request).identity_token(aina_id, tenant_id=actor.tenant_id, user_id=actor.user_id)
        return IdentityToken(token=token)

    return router
