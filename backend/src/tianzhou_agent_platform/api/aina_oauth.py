"""Connecting a user's account of an OAuth MCP server (``tools.mcp.auth.type: oauth2``; see ``aina/package/oauth``).

The shell asks for the authorization URL and sends the user there; the authorization server sends them back to
``/aina-oauth/callback``, which stores their tokens and returns them to the page they came from.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import RedirectResponse

from tianzhou_agent_platform.aina.package.ui import origin_of
from tianzhou_agent_platform.aina.protocol.models import AinaInstallation
from tianzhou_agent_platform.api.dependencies import aina_packages, request_actor, settings
from tianzhou_agent_platform.core.base import StrictModel
from tianzhou_agent_platform.core.errors import PlatformError

CALLBACK_PATH = "/aina-oauth/callback"


class AuthorizeRequest(StrictModel):
    # The shell page to return to afterwards, e.g. ``/plugin/com.example.notes``.
    return_to: str = "/plugin"


class AuthorizationUrl(StrictModel):
    authorization_url: str


def create_aina_oauth_router() -> APIRouter:
    router = APIRouter(tags=["aina-oauth"])

    @router.post("/ainas/{aina_id}/oauth/authorize", response_model=AuthorizationUrl)
    async def authorize_aina(aina_id: str, payload: AuthorizeRequest, request: Request) -> AuthorizationUrl:
        actor = request_actor(request)
        url = await aina_packages(request).start_authorization(
            aina_id, tenant_id=actor.tenant_id, user_id=actor.user_id, return_to=_safe_path(payload.return_to)
        )
        return AuthorizationUrl(authorization_url=url)

    @router.post("/ainas/{aina_id}/oauth/disconnect", response_model=AinaInstallation)
    async def disconnect_aina(aina_id: str, request: Request) -> AinaInstallation:
        actor = request_actor(request)
        return await aina_packages(request).disconnect(aina_id, tenant_id=actor.tenant_id, user_id=actor.user_id)

    @router.get(CALLBACK_PATH)
    async def aina_oauth_callback(
        request: Request,
        code: str | None = Query(default=None),
        state: str | None = Query(default=None),
        error: str | None = Query(default=None),
        error_description: str | None = Query(default=None),
    ) -> RedirectResponse:
        """The authorization server's redirect; the user lands back on the shell with the outcome in the query."""
        frontend = origin_of(settings(request).frontend_base_url)
        packages = aina_packages(request)
        return_to, aina_id = "/plugin", ""
        try:
            if state is None:
                raise PlatformError("INVALID_REQUEST", "The authorization response has no state", status_code=400)
            opened = packages.oauth.open_state(state)
            return_to, aina_id = _safe_path(opened.return_to), opened.aina_id
            if error or not code:
                raise PlatformError("AUTHENTICATION_FAILED", error_description or error or "No authorization code")
            actor = request_actor(request)
            await packages.complete_authorization(
                code=code, state=state, tenant_id=actor.tenant_id, user_id=actor.user_id
            )
            outcome = {"aina_oauth": "connected", "aina": aina_id}
        except PlatformError as exc:
            outcome = {"aina_oauth": "failed", "aina": aina_id, "message": exc.message[:300]}
        separator = "&" if "?" in return_to else "?"
        return RedirectResponse(f"{frontend}{return_to}{separator}{urlencode(outcome)}", status_code=302)

    return router


def _safe_path(value: str) -> str:
    """Only a path on the shell itself: no other origin, scheme-relative URL or control characters."""
    if not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(c) < 32 for c in value):
        return "/plugin"
    return value[:500]
