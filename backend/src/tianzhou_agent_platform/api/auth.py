from __future__ import annotations

from fastapi import APIRouter, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from tianzhou_agent_platform.api.dependencies import auth, current_user, settings
from tianzhou_agent_platform.auth.models import (
    AuthConfig,
    AuthResponse,
    LoginRequest,
    RegisterRequest,
    UserView,
)
from tianzhou_agent_platform.core.errors import PlatformError

SESSION_COOKIE = "unibot_session"
OAUTH_COOKIE = "unibot_github_oauth"


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.get("/config", response_model=AuthConfig)
    async def auth_config(request: Request) -> AuthConfig:
        app_settings = settings(request)
        return AuthConfig(
            auth_required=bool(request.app.state.auth_enforced),
            registration_enabled=app_settings.auth_registration_enabled,
            github_enabled=app_settings.github_oauth_enabled,
        )

    @router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
    async def register(payload: RegisterRequest, request: Request, response: Response) -> AuthResponse:
        user = await auth(request).register(
            email=str(payload.email),
            password=payload.password,
            name=payload.name,
        )
        _set_session_cookie(request, response, auth(request).issue_session(user))
        return AuthResponse(user=UserView.from_record(user))

    @router.post("/login", response_model=AuthResponse)
    async def login(payload: LoginRequest, request: Request, response: Response) -> AuthResponse:
        user = await auth(request).authenticate_password(email=str(payload.email), password=payload.password)
        _set_session_cookie(request, response, auth(request).issue_session(user))
        return AuthResponse(user=UserView.from_record(user))

    @router.get("/me", response_model=AuthResponse)
    async def me(request: Request) -> AuthResponse:
        return AuthResponse(user=UserView.from_record(current_user(request)))

    @router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(request: Request) -> Response:
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(
            SESSION_COOKIE,
            path="/",
            secure=settings(request).auth_cookie_secure,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get("/github")
    async def github_login(request: Request, next_path: str = Query(default="/chat", alias="next")) -> Response:
        authorization_url, oauth_cookie = auth(request).create_github_authorization(next_path=next_path)
        response = RedirectResponse(authorization_url, status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            OAUTH_COOKIE,
            oauth_cookie,
            max_age=600,
            path="/",
            secure=settings(request).auth_cookie_secure,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get("/github/callback")
    async def github_callback(
        request: Request,
        code: str | None = None,
        state_value: str | None = Query(default=None, alias="state"),
        error: str | None = None,
    ) -> Response:
        if error or not code or not state_value:
            raise PlatformError(
                "AUTHENTICATION_FAILED",
                f"GitHub OAuth was not completed: {error or 'missing callback parameters'}",
                status_code=401,
                source="auth",
                user_message="GitHub 登录未完成。",
            )
        user, next_path = await auth(request).authenticate_github(
            code=code,
            state=state_value,
            oauth_cookie=request.cookies.get(OAUTH_COOKIE),
        )
        response = RedirectResponse(
            f"{settings(request).frontend_base_url.rstrip('/')}{next_path}",
            status_code=status.HTTP_302_FOUND,
        )
        response.delete_cookie(OAUTH_COOKIE, path="/")
        _set_session_cookie(request, response, auth(request).issue_session(user))
        return response

    return router


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    app_settings = settings(request)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=app_settings.auth_session_hours * 3600,
        path="/",
        secure=app_settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )
