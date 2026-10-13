from __future__ import annotations

import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode
from uuid import UUID

import httpx
from jose import JWTError, jwt
from pydantic import EmailStr, TypeAdapter

from tianzhou_agent_platform.auth.models import OidcProvider, UserRecord
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.memory_repository import InMemoryRepository

DISPLAY_NAMES = {"google": "Google", "microsoft": "Microsoft", "apple": "Apple"}
_CONSUMER_TENANT = "9188040d-6c67-4c5b-b112-36a304b66dad"


class OidcAuthService:
    def __init__(
        self, *, settings: AgentSettings, repository: InMemoryRepository, http_client: httpx.AsyncClient | None = None
    ) -> None:
        self.settings = settings
        self.repository = repository
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=15.0)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _configuration(self, provider: OidcProvider) -> tuple[str, str, str, str, str, str]:
        if not getattr(self.settings, f"{provider}_oauth_enabled"):
            raise PlatformError(
                "DEPENDENCY_FAILED",
                f"{provider} OAuth is not configured",
                status_code=503,
                source="auth",
                user_message=f"{DISPLAY_NAMES[provider]} 登录尚未配置。",
            )
        client_id = getattr(self.settings, f"{provider}_oauth_client_id")
        client_secret = getattr(self.settings, f"{provider}_oauth_client_secret").get_secret_value()
        callback = getattr(self.settings, f"{provider}_oauth_callback_url")
        if provider == "google":
            authorize = "https://accounts.google.com/o/oauth2/v2/auth"
            token = "https://oauth2.googleapis.com/token"
            keys = "https://www.googleapis.com/oauth2/v3/certs"
        elif provider == "microsoft":
            base = f"https://login.microsoftonline.com/{self.settings.microsoft_oauth_tenant}"
            authorize, token, keys = (
                f"{base}/oauth2/v2.0/authorize",
                f"{base}/oauth2/v2.0/token",
                f"{base}/discovery/v2.0/keys",
            )
        else:
            authorize, token, keys = (
                "https://appleid.apple.com/auth/authorize",
                "https://appleid.apple.com/auth/token",
                "https://appleid.apple.com/auth/keys",
            )
        return client_id, client_secret, callback, authorize, token, keys

    def create_authorization(self, provider: OidcProvider, *, next_path: str) -> tuple[str, str]:
        client_id, _, callback, authorize, _, _ = self._configuration(provider)
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        claims = {
            "typ": f"{provider}-oauth",
            "state": state,
            "nonce": nonce,
            "next": next_path,
            "iat": now,
            "exp": now + timedelta(minutes=10),
            "iss": self.settings.auth_issuer,
        }
        params = {
            "client_id": client_id,
            "redirect_uri": callback,
            "response_type": "code",
            "scope": "name email" if provider == "apple" else "openid email profile",
            "state": state,
            "nonce": nonce,
            "response_mode": "form_post" if provider == "apple" else "query",
        }
        if provider != "apple":
            verifier = secrets.token_urlsafe(64)
            claims["verifier"] = verifier
            params["code_challenge"] = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
            )
            params["code_challenge_method"] = "S256"
        cookie = jwt.encode(claims, self.settings.auth_secret.get_secret_value(), algorithm="HS256")
        return f"{authorize}?{urlencode(params)}", cookie

    async def authenticate(
        self, provider: OidcProvider, *, code: str, state: str, oauth_cookie: str | None, apple_user: str | None = None
    ) -> tuple[UserRecord, str]:
        client_id, client_secret, callback, _, token_url, keys_url = self._configuration(provider)
        try:
            if not oauth_cookie:
                raise ValueError("OAuth cookie is missing")
            pending = jwt.decode(
                oauth_cookie,
                self.settings.auth_secret.get_secret_value(),
                algorithms=["HS256"],
                issuer=self.settings.auth_issuer,
                options={"require_exp": True},
            )
            if pending.get("typ") != f"{provider}-oauth" or not secrets.compare_digest(
                str(pending.get("state", "")).encode(), state.encode()
            ):
                raise ValueError("OAuth state or provider did not match")
            nonce = pending.get("nonce")
            if not isinstance(nonce, str) or not nonce:
                raise ValueError("OAuth nonce is missing")
            body = {
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": callback,
                "code": code,
                "grant_type": "authorization_code",
            }
            if provider != "apple":
                verifier = pending.get("verifier")
                if not isinstance(verifier, str) or not verifier:
                    raise ValueError("PKCE verifier is missing")
                body["code_verifier"] = verifier
            token_response = await self._client.post(token_url, data=body, headers={"Accept": "application/json"})
            token_response.raise_for_status()
            payload = token_response.json()
            id_token = payload.get("id_token") if isinstance(payload, dict) else None
            if not isinstance(id_token, str) or not id_token or "error" in payload:
                raise ValueError("Provider did not return an ID token")
            keys_response = await self._client.get(keys_url, headers={"Accept": "application/json"})
            keys_response.raise_for_status()
            access_token = payload.get("access_token")
            profile = self._verify_id_token(
                provider,
                id_token,
                keys_response.json(),
                client_id,
                nonce,
                access_token if isinstance(access_token, str) else None,
            )
            subject = profile.get("sub")
            if not isinstance(subject, str) or not subject:
                raise ValueError("ID token subject is missing")
            email = str(TypeAdapter(EmailStr).validate_python(profile.get("email")))
            if provider != "microsoft" and profile.get("email_verified") not in (True, "true"):
                raise ValueError("Provider email is not verified")
        except (JWTError, httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise _oidc_error(provider) from exc
        name = _apple_name(apple_user) if provider == "apple" else profile.get("name")
        picture = profile.get("picture")
        # Microsoft subjects are tenant-scoped. Use the verified issuer with the subject for all providers.
        issuer = "https://accounts.google.com" if provider == "google" else profile["iss"]
        user = await self.repository.upsert_oidc_user(
            provider=provider,
            identity=f"{issuer}|{subject}",
            email=email,
            name=name.strip()[:80] if isinstance(name, str) and name.strip() else None,
            avatar_url=picture if isinstance(picture, str) and len(picture) <= 2083 else None,
        )
        return user, str(pending.get("next", "/chat"))

    def _verify_id_token(
        self, provider: OidcProvider, token: str, jwks: Any, client_id: str, nonce: str, access_token: str | None
    ) -> dict[str, Any]:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
            raise ValueError("Unsupported ID token signing algorithm")
        keys = jwks.get("keys") if isinstance(jwks, dict) else None
        if not isinstance(keys, list):
            raise ValueError("Invalid signing keys")
        key = next((item for item in keys if isinstance(item, dict) and item.get("kid") == header["kid"]), None)
        if key is None or key.get("kty") != "RSA" or key.get("use", "sig") != "sig":
            raise ValueError("ID token signing key was not found")
        issuer: str | list[str] = ["https://accounts.google.com", "accounts.google.com"]
        if provider == "apple":
            issuer = "https://appleid.apple.com"
        elif provider == "microsoft":
            # These claims only select the expected issuer; jwt.decode still verifies the signature and claims.
            unverified = jwt.get_unverified_claims(token)
            tenant_id = unverified.get("tid")
            if not isinstance(tenant_id, str):
                raise ValueError("Microsoft tenant ID is missing")
            tenant = str(UUID(tenant_id))
            configured = self.settings.microsoft_oauth_tenant
            if (
                configured == "consumers"
                and tenant != _CONSUMER_TENANT
                or configured == "organizations"
                and tenant == _CONSUMER_TENANT
                or configured not in {"common", "organizations", "consumers"}
                and tenant != configured
            ):
                raise ValueError("Microsoft tenant is not allowed")
            issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"
            key_issuer = key.get("issuer")
            if not isinstance(key_issuer, str) or key_issuer.replace("{tenantid}", tenant) != issuer:
                raise ValueError("Microsoft signing key issuer did not match")
        profile = jwt.decode(
            token,
            key,
            algorithms=["RS256"],
            audience=client_id,
            issuer=issuer,
            access_token=access_token,
            options={
                "require_exp": True,
                "require_iat": True,
                "require_sub": True,
                "require_iss": True,
                "require_aud": True,
            },
        )
        if not isinstance(profile.get("nonce"), str) or not secrets.compare_digest(
            profile["nonce"].encode(), nonce.encode()
        ):
            raise ValueError("ID token nonce did not match")
        if profile.get("azp", client_id) != client_id:
            raise ValueError("ID token authorized party did not match")
        if isinstance(profile.get("aud"), list) and len(profile["aud"]) > 1 and profile.get("azp") != client_id:
            raise ValueError("ID token authorized party is missing")
        return profile


def _apple_name(user: str | None) -> str | None:
    if not user:
        return None
    try:
        data = json.loads(user)
    except ValueError:
        return None
    name = data.get("name") if isinstance(data, dict) else None
    if not isinstance(name, dict):
        return None
    return (
        " ".join(value.strip() for key in ("firstName", "lastName") if isinstance(value := name.get(key), str)).strip()
        or None
    )


def _oidc_error(provider: OidcProvider) -> PlatformError:
    return PlatformError(
        "AUTHENTICATION_FAILED",
        f"{provider} OAuth authentication failed",
        status_code=401,
        source="auth",
        user_message=f"{DISPLAY_NAMES[provider]} 登录失败，请重试。",
    )
