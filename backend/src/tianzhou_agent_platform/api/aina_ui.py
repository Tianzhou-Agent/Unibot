"""Routes behind the canvas and widgets of AINA 2.0 packages (design §9).

``/aina-ui/...`` serves packaged UI files to sandboxed iframes and needs no session. The other routes are called by
the host shell for the signed-in user, never by the iframe: the UI bridge relays an iframe's request only after the
host has applied its own checks and confirmations.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Request, Response

from tianzhou_agent_platform.aina.package.service import package_runtime
from tianzhou_agent_platform.aina.package.ui import origin_of, ui_headers
from tianzhou_agent_platform.api.dependencies import aina_packages, repository, request_actor, settings
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.errors import not_found

AINA_UI_PREFIX = "/aina-ui"


class AppToolCall(StrictModel):
    arguments: dict[str, Any]
    source: Literal["canvas", "widget"] = "canvas"
    # Set by the shell after it asked the user; the AINA's UI cannot set it.
    confirmed: bool = False


class IdentityToken(StrictModel):
    token: str


def create_aina_ui_router() -> APIRouter:
    router = APIRouter(tags=["aina-ui"])

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
    async def call_aina_app_tool(aina_id: str, tool_name: str, payload: AppToolCall, request: Request) -> dict[str, Any]:
        """A tool call relayed from the AINA's canvas or widget (design §9.4 ``callTool``)."""
        actor = request_actor(request)
        return await aina_packages(request).call_app_tool(
            aina_id,
            tool_name,
            payload.arguments,
            tenant_id=actor.tenant_id,
            user_id=actor.user_id,
            source=payload.source,
            confirmed=payload.confirmed,
        )

    @router.post("/ainas/{aina_id}/identity-token", response_model=IdentityToken)
    async def aina_identity_token(aina_id: str, request: Request) -> IdentityToken:
        """``getIdentityToken()`` of a URL canvas (design §9.1)."""
        actor = request_actor(request)
        token = await aina_packages(request).identity_token(aina_id, tenant_id=actor.tenant_id, user_id=actor.user_id)
        return IdentityToken(token=token)

    return router
