from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from jose import JWTError, jwt
from pwdlib import PasswordHash

from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.core.repository import InMemoryRepository

_SESSION_TOKEN_TYPE = "session"
_OAUTH_TOKEN_TYPE = "github-oauth"


class AuthService:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        repository: InMemoryRepository,
        github_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self._password_hash = PasswordHash.recommended()
        self._dummy_hash: str | None = None
        self._owns_github_client = github_http_client is None
        self._github_client = github_http_client or httpx.AsyncClient(timeout=15.0)

    async def aclose(self) -> None:
        if self._owns_github_client:
            await self._github_client.aclose()

    async def register(self, *, email: str, password: str, name: str) -> UserRecord:
        if not self.settings.auth_registration_enabled:
            raise PlatformError(
                "PERMISSION_DENIED",
                "User registration is disabled",
                status_code=403,
                source="auth",
                user_message="当前系统未开放注册。",
            )
        user = UserRecord(
            id=f"user_{uuid4().hex}",
            email=email.strip().lower(),
            name=name.strip(),
            password_hash=self._password_hash.hash(password),
        )
        return await self.repository.create_user(user)

    async def authenticate_password(self, *, email: str, password: str) -> UserRecord:
        user = await self.repository.find_user_by_email(email.strip().lower())
        if self._dummy_hash is None and (user is None or user.password_hash is None):
            self._dummy_hash = self._password_hash.hash("unibot-dummy-password")
        password_hash = user.password_hash if user and user.password_hash else self._dummy_hash
        assert password_hash is not None
        valid = self._password_hash.verify(password, password_hash)
        if user is None or user.password_hash is None or not valid:
            raise PlatformError(
                "AUTHENTICATION_FAILED",
                "Invalid email or password",
                status_code=401,
                source="auth",
                user_message="邮箱或密码错误。",
            )
        return user

    def issue_session(self, user: UserRecord) -> str:
        now = datetime.now(UTC)
        payload = {
            "typ": _SESSION_TOKEN_TYPE,
            "sub": user.id,
            "iat": now,
            "exp": now + timedelta(hours=self.settings.auth_session_hours),
            "iss": self.settings.auth_issuer,
        }
        return jwt.encode(payload, self.settings.auth_secret.get_secret_value(), algorithm="HS256")

    async def resolve_session(self, token: str | None) -> UserRecord | None:
        if not token:
            return None
        try:
            claims = jwt.decode(
                token,
                self.settings.auth_secret.get_secret_value(),
                algorithms=["HS256"],
                issuer=self.settings.auth_issuer,
            )
        except JWTError:
            return None
        if claims.get("typ") != _SESSION_TOKEN_TYPE or not isinstance(claims.get("sub"), str):
            return None
        return await self.repository.find_user_by_id(claims["sub"])

    def create_github_authorization(self, *, next_path: str) -> tuple[str, str]:
        client_id, _ = self._github_credentials()
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        now = datetime.now(UTC)
        oauth_cookie = jwt.encode(
            {
                "typ": _OAUTH_TOKEN_TYPE,
                "state": state,
                "verifier": verifier,
                "next": _safe_next_path(next_path),
                "iat": now,
                "exp": now + timedelta(minutes=10),
                "iss": self.settings.auth_issuer,
            },
            self.settings.auth_secret.get_secret_value(),
            algorithm="HS256",
        )
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": self.settings.github_oauth_callback_url,
                "scope": "user:email",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        return f"https://github.com/login/oauth/authorize?{query}", oauth_cookie

    async def authenticate_github(
        self,
        *,
        code: str,
        state: str,
        oauth_cookie: str | None,
    ) -> tuple[UserRecord, str]:
        client_id, client_secret = self._github_credentials()
        claims = self._decode_oauth_cookie(oauth_cookie)
        if not secrets.compare_digest(str(claims.get("state", "")), state):
            raise _oauth_error("GitHub OAuth state did not match")
        verifier = claims.get("verifier")
        if not isinstance(verifier, str) or not verifier:
            raise _oauth_error("GitHub OAuth PKCE verifier is missing")

        try:
            token_response = await self._github_client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "redirect_uri": self.settings.github_oauth_callback_url,
                    "code_verifier": verifier,
                },
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            access_token = token_payload.get("access_token") if isinstance(token_payload, dict) else None
            if not isinstance(access_token, str) or not access_token:
                raise _oauth_error("GitHub did not return an access token")

            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "X-GitHub-Api-Version": self.settings.github_api_version,
            }
            profile_response = await self._github_client.get("https://api.github.com/user", headers=headers)
            email_response = await self._github_client.get("https://api.github.com/user/emails", headers=headers)
            profile_response.raise_for_status()
            email_response.raise_for_status()
            profile = profile_response.json()
            if not isinstance(profile, dict):
                raise _oauth_error("GitHub returned an invalid user profile")
            email = _verified_github_email(email_response.json())
        except (httpx.HTTPError, ValueError) as exc:
            raise _oauth_error("GitHub OAuth request failed") from exc
        github_id = profile.get("id")
        github_login = profile.get("login")
        if github_id is None or not isinstance(github_login, str) or not github_login:
            raise _oauth_error("GitHub returned an invalid user profile")
        user = await self.repository.upsert_github_user(
            github_id=str(github_id),
            github_login=github_login,
            email=email,
            name=_github_display_name(profile, github_login),
            avatar_url=profile.get("avatar_url") if isinstance(profile.get("avatar_url"), str) else None,
        )
        return user, _safe_next_path(str(claims.get("next", "/chat")))

    def _decode_oauth_cookie(self, token: str | None) -> dict[str, Any]:
        if not token:
            raise _oauth_error("GitHub OAuth cookie is missing")
        try:
            claims = jwt.decode(
                token,
                self.settings.auth_secret.get_secret_value(),
                algorithms=["HS256"],
                issuer=self.settings.auth_issuer,
            )
        except JWTError as exc:
            raise _oauth_error("GitHub OAuth cookie is invalid or expired") from exc
        if claims.get("typ") != _OAUTH_TOKEN_TYPE:
            raise _oauth_error("GitHub OAuth cookie has an invalid type")
        return claims

    def _github_credentials(self) -> tuple[str, str]:
        client_id = self.settings.github_oauth_client_id
        client_secret = self.settings.github_oauth_client_secret
        if not client_id or client_secret is None:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "GitHub OAuth is not configured",
                status_code=503,
                source="auth",
                user_message="GitHub 登录尚未配置。",
            )
        return client_id, client_secret.get_secret_value()


def _verified_github_email(payload: Any) -> str:
    if not isinstance(payload, list):
        raise _oauth_error("GitHub returned an invalid email response")
    verified = [item for item in payload if isinstance(item, dict) and item.get("verified") is True]
    selected = next((item for item in verified if item.get("primary") is True), None)
    selected = selected or (verified[0] if verified else None)
    email = selected.get("email") if selected else None
    if not isinstance(email, str) or not email:
        raise PlatformError(
            "AUTHENTICATION_FAILED",
            "GitHub account has no verified email address",
            status_code=401,
            source="auth",
            user_message="GitHub 账号没有可用的已验证邮箱。",
        )
    return email.strip().lower()


def _github_display_name(profile: dict[str, Any], login: str) -> str:
    name = profile.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()[:80]
    return login[:80]


def _safe_next_path(value: str) -> str:
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/chat"
    if any(ord(character) < 32 for character in value):
        return "/chat"
    return value


def _oauth_error(message: str) -> PlatformError:
    return PlatformError(
        "AUTHENTICATION_FAILED",
        message,
        status_code=401,
        source="auth",
        user_message="GitHub 登录失败，请重试。",
    )
