from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Form, Query, Request, Response, status
from fastapi.responses import RedirectResponse

from tianzhou_agent_platform.api.dependencies import auth, current_user, settings
from tianzhou_agent_platform.auth.models import (
    AuthConfig,
    AuthResponse,
    LoginRequest,
    RegisterRequest,
    UserRecord,
    UserView,
    OidcProvider,
)
from tianzhou_agent_platform.auth.oidc import DISPLAY_NAMES
from tianzhou_agent_platform.auth.service import _safe_next_path
from tianzhou_agent_platform.core.errors import PlatformError

SESSION_COOKIE = "unibot_session"
OAUTH_COOKIE = "unibot_github_oauth"
HUAWEI_OAUTH_COOKIE = "unibot_huawei_oauth"


def create_auth_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["auth"])

    @router.get("/config", response_model=AuthConfig)
    async def auth_config(request: Request) -> AuthConfig:
        app_settings = settings(request)
        return AuthConfig(
            auth_required=bool(request.app.state.auth_enforced),
            default_language=app_settings.default_language,
            registration_enabled=app_settings.registration_enabled,
            github_enabled=app_settings.github_oauth_enabled,
            huawei_enabled=app_settings.huawei_oauth_enabled,
            google_enabled=app_settings.google_oauth_enabled,
            microsoft_enabled=app_settings.microsoft_oauth_enabled,
            apple_enabled=app_settings.apple_oauth_enabled,
        )

    @router.post("/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
    async def register(payload: RegisterRequest, request: Request, response: Response) -> AuthResponse:
        user = await auth(request).register(
            email=str(payload.email),
            password=payload.password,
            name=payload.name,
        )
        _set_session_cookie(request, response, auth(request).issue_session(user))
        return AuthResponse(user=_user_view(request, user))

    @router.post("/login", response_model=AuthResponse)
    async def login(payload: LoginRequest, request: Request, response: Response) -> AuthResponse:
        user = await auth(request).authenticate_password(email=str(payload.email), password=payload.password)
        _set_session_cookie(request, response, auth(request).issue_session(user))
        return AuthResponse(user=_user_view(request, user))

    @router.get("/me", response_model=AuthResponse)
    async def me(request: Request) -> AuthResponse:
        return AuthResponse(user=_user_view(request, current_user(request)))

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

    @router.get("/huawei")
    async def huawei_login(request: Request, next_path: str = Query(default="/chat", alias="next")) -> Response:
        authorization_url, oauth_cookie = auth(request).create_huawei_authorization(next_path=next_path)
        response = RedirectResponse(authorization_url, status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            HUAWEI_OAUTH_COOKIE,
            oauth_cookie,
            max_age=600,
            path="/",
            secure=settings(request).auth_cookie_secure,
            httponly=True,
            samesite="lax",
        )
        return response

    @router.get("/huawei/callback")
    async def huawei_callback(
        request: Request,
        code: str | None = None,
        state_value: str | None = Query(default=None, alias="state"),
        error: str | None = None,
    ) -> Response:
        if error or not code or not state_value:
            raise PlatformError(
                "AUTHENTICATION_FAILED",
                f"Huawei OAuth was not completed: {error or 'missing callback parameters'}",
                status_code=401,
                source="auth",
                user_message="华为登录未完成。",
            )
        user, next_path = await auth(request).authenticate_huawei(
            code=code,
            state=state_value,
            oauth_cookie=request.cookies.get(HUAWEI_OAUTH_COOKIE),
        )
        response = RedirectResponse(
            f"{settings(request).frontend_base_url.rstrip('/')}{next_path}",
            status_code=status.HTTP_302_FOUND,
        )
        response.delete_cookie(HUAWEI_OAUTH_COOKIE, path="/")
        _set_session_cookie(request, response, auth(request).issue_session(user))
        return response

    @router.get("/{provider}")
    async def oidc_login(
        provider: OidcProvider, request: Request, next_path: str = Query(default="/chat", alias="next")
    ) -> Response:
        authorization_url, oauth_cookie = auth(request).oidc.create_authorization(
            provider, next_path=_safe_next_path(next_path)
        )
        response = RedirectResponse(authorization_url, status_code=status.HTTP_302_FOUND)
        response.set_cookie(
            f"unibot_{provider}_oauth",
            oauth_cookie,
            max_age=600,
            path="/",
            secure=settings(request).auth_cookie_secure,
            httponly=True,
            samesite="none" if provider == "apple" else "lax",
        )
        return response

    @router.get("/{provider}/callback")
    async def oidc_callback(
        provider: Literal["google", "microsoft"],
        request: Request,
        code: str | None = None,
        state_value: str | None = Query(default=None, alias="state"),
        error: str | None = None,
    ) -> Response:
        return await _complete_oidc_login(request, provider, code, state_value, error)

    @router.post("/apple/callback")
    async def apple_callback(
        request: Request,
        code: str | None = Form(default=None),
        state_value: str | None = Form(default=None, alias="state"),
        error: str | None = Form(default=None),
        user: str | None = Form(default=None),
    ) -> Response:
        return await _complete_oidc_login(request, "apple", code, state_value, error, apple_user=user)

    return router


async def _complete_oidc_login(
    request: Request,
    provider: OidcProvider,
    code: str | None,
    state_value: str | None,
    error: str | None,
    *,
    apple_user: str | None = None,
) -> Response:
    if error or not code or not state_value:
        raise PlatformError(
            "AUTHENTICATION_FAILED",
            f"{provider} OAuth was not completed",
            status_code=401,
            source="auth",
            user_message=f"{DISPLAY_NAMES[provider]} 登录未完成。",
        )
    cookie_name = f"unibot_{provider}_oauth"
    user, next_path = await auth(request).oidc.authenticate(
        provider, code=code, state=state_value, oauth_cookie=request.cookies.get(cookie_name), apple_user=apple_user
    )
    response = RedirectResponse(
        f"{settings(request).frontend_base_url.rstrip('/')}{_safe_next_path(next_path)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
    response.delete_cookie(
        cookie_name,
        path="/",
        secure=settings(request).auth_cookie_secure,
        httponly=True,
        samesite="none" if provider == "apple" else "lax",
    )
    _set_session_cookie(request, response, auth(request).issue_session(user))
    return response


def _user_view(request: Request, user: UserRecord) -> UserView:
    app_settings = settings(request)
    return UserView.from_record(
        user,
        is_admin=app_settings.is_platform_admin(
            user_id=user.id,
            email=str(user.email),
            github_login=user.github_login,
        ),
    )


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
