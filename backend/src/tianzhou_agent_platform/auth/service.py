from __future__ import annotations

import base64
import hashlib
import secrets
import ssl
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from jose import JWTError, jwt
from pydantic import EmailStr, TypeAdapter
from pwdlib import PasswordHash

from tianzhou_agent_platform.auth.models import UserRecord
from tianzhou_agent_platform.auth.oidc import OidcAuthService
from tianzhou_agent_platform.config import DEV_USER_EMAIL, DEV_USER_ID, AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository

_SESSION_TOKEN_TYPE = "session"
_OAUTH_TOKEN_TYPE = "github-oauth"


class AuthService:
    def __init__(
        self,
        *,
        settings: AgentSettings,
        repository: InMemoryRepository,
        github_http_client: httpx.AsyncClient | None = None,
        huawei_http_client: httpx.AsyncClient | None = None,
        oidc_http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self._password_hash = PasswordHash.recommended()
        self._dummy_hash: str | None = None
        self._owns_github_client = github_http_client is None
        self._github_client = github_http_client or httpx.AsyncClient(timeout=15.0)
        self._owns_huawei_client = huawei_http_client is None
        huawei_verify = (
            ssl.create_default_context(cafile=settings.huawei_oauth_ca_bundle)
            if settings.huawei_oauth_ca_bundle
            else settings.huawei_oauth_ssl_verify
        )
        self._huawei_client = huawei_http_client or httpx.AsyncClient(timeout=15.0, verify=huawei_verify)
        self.oidc = OidcAuthService(settings=settings, repository=repository, http_client=oidc_http_client)

    async def aclose(self) -> None:
        if self._owns_github_client:
            await self._github_client.aclose()
        if self._owns_huawei_client:
            await self._huawei_client.aclose()
        await self.oidc.aclose()

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

    async def ensure_dev_user(self) -> UserRecord:
        """Return the fixed local user used when UNIBOT_DEV_AUTH_BYPASS is on."""
        user = await self.repository.find_user_by_id(DEV_USER_ID)
        if user is not None:
            return user
        try:
            return await self.repository.create_user(
                UserRecord(id=DEV_USER_ID, email=DEV_USER_EMAIL, name="Dev User")
            )
        except PlatformError:
            user = await self.repository.find_user_by_id(DEV_USER_ID)
            if user is None:
                raise
            return user

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

    def create_huawei_authorization(self, *, next_path: str) -> tuple[str, str]:
        base_url, client_id, _ = self._huawei_credentials()
        state = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        oauth_cookie = jwt.encode(
            {
                "typ": "huawei-oauth",
                "state": state,
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
                "redirect_uri": self.settings.huawei_oauth_callback_url,
                "scope": "base.profile",
                "response_type": "code",
                "state": state,
            }
        )
        return f"{base_url}/saaslogin1/oauth2/authorize?{query}", oauth_cookie

    async def authenticate_huawei(
        self, *, code: str, state: str, oauth_cookie: str | None
    ) -> tuple[UserRecord, str]:
        base_url, client_id, client_secret = self._huawei_credentials()
        claims = self._decode_oauth_cookie(oauth_cookie, provider="Huawei")
        if not secrets.compare_digest(str(claims.get("state", "")), state):
            raise _oauth_error("Huawei OAuth state did not match", provider="Huawei")
        try:
            token_response = await self._huawei_client.post(
                f"{base_url}/saaslogin1/oauth2/accesstoken",
                headers={"Accept": "application/json"},
                json={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            token_response.raise_for_status()
            token_payload = token_response.json()
            access_token = token_payload.get("access_token") if isinstance(token_payload, dict) else None
            if not isinstance(access_token, str) or not access_token or "error" in token_payload:
                raise _oauth_error("Huawei did not return an access token", provider="Huawei")
            profile_response = await self._huawei_client.get(
                f"{base_url}/saaslogin1/oauth2/userinfo",
                headers={"Accept": "application/json", "Authorization": f"Bearer {access_token}"},
                params={"access_token": access_token, "client_id": client_id, "scope": "base.profile"},
            )
            profile_response.raise_for_status()
            profile = profile_response.json()
            if not isinstance(profile, dict) or "error" in profile:
                raise _oauth_error("Huawei returned an invalid user profile", provider="Huawei")
            huawei_id = profile.get("globalUserID")
            name = profile.get("givenName")
            if not isinstance(huawei_id, str) or not huawei_id.strip() or len(huawei_id) > 255:
                raise _oauth_error("Huawei returned an invalid user ID", provider="Huawei")
            email = TypeAdapter(EmailStr).validate_python(profile.get("email"))
        except (httpx.HTTPError, ValueError) as exc:
            raise _oauth_error("Huawei OAuth request or user profile is invalid", provider="Huawei") from exc
        user = await self.repository.upsert_huawei_user(
            huawei_id=huawei_id,
            email=str(email),
            name=name.strip()[:80] if isinstance(name, str) and name.strip() else huawei_id[:80],
        )
        return user, _safe_next_path(str(claims.get("next", "/chat")))

    def _huawei_credentials(self) -> tuple[str, str, str]:
        base_url = self.settings.huawei_oauth_base_url
        client_id = self.settings.huawei_oauth_client_id
        client_secret = self.settings.huawei_oauth_client_secret
        if not base_url or not client_id or client_secret is None:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "Huawei OAuth is not configured",
                status_code=503,
                source="auth",
                user_message="华为登录尚未配置。",
            )
        return base_url.rstrip("/"), client_id, client_secret.get_secret_value()

    def _decode_oauth_cookie(self, token: str | None, *, provider: str = "GitHub") -> dict[str, Any]:
        if not token:
            raise _oauth_error(f"{provider} OAuth cookie is missing", provider=provider)
        try:
            claims = jwt.decode(
                token,
                self.settings.auth_secret.get_secret_value(),
                algorithms=["HS256"],
                issuer=self.settings.auth_issuer,
            )
        except JWTError as exc:
            raise _oauth_error(f"{provider} OAuth cookie is invalid or expired", provider=provider) from exc
        if claims.get("typ") != f"{provider.lower()}-oauth":
            raise _oauth_error(f"{provider} OAuth cookie has an invalid type", provider=provider)
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


def _oauth_error(message: str, *, provider: str = "GitHub") -> PlatformError:
    return PlatformError(
        "AUTHENTICATION_FAILED",
        message,
        status_code=401,
        source="auth",
        user_message=f"{'华为' if provider == 'Huawei' else provider} 登录失败，请重试。",
    )
