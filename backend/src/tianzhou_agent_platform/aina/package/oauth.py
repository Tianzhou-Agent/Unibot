"""MCP authorization (OAuth 2.1) for package MCP servers with ``tools.mcp.auth.type: oauth2`` (design §5.3).

Unibot follows the MCP authorization spec as an OAuth client:

1. **Discovery.** The MCP server's protected resource metadata (RFC 9728), from the ``resource_metadata`` of its
   ``401`` challenge or its well-known URL, names the authorization server; that server's metadata (RFC 8414, or
   OpenID Connect discovery) gives its endpoints. PKCE ``S256`` support is required.
2. **Client registration.** Unibot registers itself once per AINA with dynamic client registration (RFC 7591) as a
   public client, and keeps the registration across upgrades of the package.
3. **Authorization.** Each user connects their own account: authorization code with PKCE and the ``resource``
   indicator (RFC 8707). The ``state`` is sealed and bound to the user who started the flow.
4. **Tokens** are sealed per installation, refreshed shortly before they expire, and sent as ``Authorization: Bearer``.

Every request the host makes here (metadata, registration, tokens) passes the same destination policy as MCP
servers; only the authorization page itself is opened by the user's browser.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

import httpx
from pydantic import Field

from tianzhou_agent_platform.aina.package.secrets import SecretBox
from tianzhou_agent_platform.core.base import StrictModel, utc_now
from tianzhou_agent_platform.core.errors import PlatformError

# The installation secret that holds the user's sealed tokens; not a valid configuration property name.
OAUTH_SECRET = "@oauth"
STATE_SECONDS = 600
REFRESH_MARGIN = timedelta(seconds=60)
_RESOURCE_METADATA = re.compile(r'resource_metadata="([^"]+)"')


class OAuthServer(StrictModel):
    """How Unibot reaches an AINA's authorization server, and the client it registered there."""

    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    client_id: str
    # Sealed; most servers register Unibot as a public client without one.
    client_secret: str | None = None
    redirect_uri: str
    resource: str
    scopes: list[str] = Field(default_factory=list)
    registered_at: datetime = Field(default_factory=utc_now)


class OAuthTokens(StrictModel):
    access_token: str
    refresh_token: str | None = None
    expires_at: datetime | None = None
    scope: str | None = None

    @property
    def expiring(self) -> bool:
        return self.expires_at is not None and self.expires_at - REFRESH_MARGIN <= utc_now()


class OAuthState(StrictModel):
    aina_id: str
    tenant_id: str
    user_id: str
    verifier: str
    return_to: str
    expires: float


def oauth_failure(message: str, *, status_code: int = 502) -> PlatformError:
    return PlatformError("AUTHENTICATION_FAILED", message, status_code=status_code, source="mcp_oauth")


class McpOAuth:
    def __init__(
        self,
        *,
        box: SecretBox,
        redirect_uri: str,
        require_destination: Callable[[str], None],
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self.box = box
        self.redirect_uri = redirect_uri
        self.require_destination = require_destination
        self.http = http or httpx.AsyncClient(timeout=15.0, follow_redirects=False)

    # --- discovery and registration ---------------------------------------------------------------------------

    async def register_client(self, *, aina_id: str, mcp_url: str, scopes: list[str]) -> OAuthServer:
        """Discover the MCP server's authorization server and register Unibot there."""
        resource_metadata = await self._resource_metadata(mcp_url)
        servers = resource_metadata.get("authorization_servers") or []
        if not servers:
            raise oauth_failure("The MCP server's protected resource metadata names no authorization server")
        issuer = str(servers[0])
        metadata = await self._server_metadata(issuer)
        if "S256" not in (metadata.get("code_challenge_methods_supported") or []):
            raise oauth_failure("The authorization server does not support PKCE with S256, which MCP requires")
        registration = metadata.get("registration_endpoint")
        if not registration:
            raise oauth_failure(
                "The authorization server offers no dynamic client registration; Unibot cannot connect to it yet"
            )
        wanted = scopes or [str(scope) for scope in resource_metadata.get("scopes_supported") or []]
        body: dict[str, Any] = {
            "client_name": "Unibot",
            "redirect_uris": [self.redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        }
        if wanted:
            body["scope"] = " ".join(wanted)
        registered = await self._post_json(str(registration), body)
        client_id = registered.get("client_id")
        if not isinstance(client_id, str) or not client_id:
            raise oauth_failure("The authorization server did not return a client_id")
        client_secret = registered.get("client_secret")
        return OAuthServer(
            issuer=issuer,
            authorization_endpoint=_url(metadata, "authorization_endpoint", browser=True),
            token_endpoint=_url(metadata, "token_endpoint"),
            client_id=client_id,
            client_secret=self.box.seal(str(client_secret), context=_client_context(aina_id))
            if client_secret
            else None,
            redirect_uri=self.redirect_uri,
            resource=str(resource_metadata.get("resource") or mcp_url),
            scopes=wanted,
        )

    async def _resource_metadata(self, mcp_url: str) -> dict[str, Any]:
        """RFC 9728: the URL from the server's 401 challenge if it gives one, else the well-known locations."""
        candidates: list[str] = []
        self.require_destination(mcp_url)
        try:
            probe = await self.http.post(
                mcp_url,
                json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
                headers={"Accept": "application/json, text/event-stream"},
            )
        except httpx.HTTPError as exc:
            raise oauth_failure(f"The MCP server is unreachable: {exc}") from exc
        challenge = _RESOURCE_METADATA.search(probe.headers.get("www-authenticate", ""))
        if challenge:
            candidates.append(challenge.group(1))
        parts = urlsplit(mcp_url)
        path = parts.path.rstrip("/")
        if path:
            candidates.append(
                urlunsplit((parts.scheme, parts.netloc, f"/.well-known/oauth-protected-resource{path}", "", ""))
            )
        candidates.append(urlunsplit((parts.scheme, parts.netloc, "/.well-known/oauth-protected-resource", "", "")))
        for candidate in candidates:
            found = await self._get_json(candidate)
            if found is not None:
                return found
        raise oauth_failure("The MCP server publishes no OAuth protected resource metadata")

    async def _server_metadata(self, issuer: str) -> dict[str, Any]:
        """RFC 8414 or OpenID Connect discovery, including issuers with a path."""
        parts = urlsplit(issuer)
        path = parts.path.rstrip("/")
        base = (parts.scheme, parts.netloc)
        candidates = [
            urlunsplit((*base, f"/.well-known/oauth-authorization-server{path}", "", "")),
            urlunsplit((*base, f"/.well-known/openid-configuration{path}", "", "")),
        ]
        if path:
            candidates.append(urlunsplit((*base, f"{path}/.well-known/openid-configuration", "", "")))
        for candidate in candidates:
            found = await self._get_json(candidate)
            if found is not None:
                if found.get("issuer") not in (None, issuer, issuer.rstrip("/")):
                    raise oauth_failure("The authorization server's metadata names another issuer")
                return found
        raise oauth_failure(f"No authorization server metadata at {issuer}")

    # --- authorization ------------------------------------------------------------------------------------------

    def authorization_url(
        self, server: OAuthServer, *, aina_id: str, tenant_id: str, user_id: str, return_to: str
    ) -> str:
        verifier = secrets.token_urlsafe(48)
        state = OAuthState(
            aina_id=aina_id,
            tenant_id=tenant_id,
            user_id=user_id,
            verifier=verifier,
            return_to=return_to,
            expires=time.time() + STATE_SECONDS,
        )
        query = {
            "response_type": "code",
            "client_id": server.client_id,
            "redirect_uri": server.redirect_uri,
            "state": self.box.seal(state.model_dump_json(), context=_STATE_CONTEXT),
            "code_challenge": _challenge(verifier),
            "code_challenge_method": "S256",
            "resource": server.resource,
        }
        if server.scopes:
            query["scope"] = " ".join(server.scopes)
        separator = "&" if urlsplit(server.authorization_endpoint).query else "?"
        return f"{server.authorization_endpoint}{separator}{urlencode(query)}"

    def open_state(self, state: str) -> OAuthState:
        try:
            opened = OAuthState.model_validate_json(self.box.open(state, context=_STATE_CONTEXT))
        except (PlatformError, ValueError) as exc:
            raise oauth_failure("The authorization response is not one Unibot started", status_code=400) from exc
        if opened.expires < time.time():
            raise oauth_failure("The authorization took too long; connect again", status_code=400)
        return opened

    async def exchange(self, server: OAuthServer, *, aina_id: str, code: str, verifier: str) -> OAuthTokens:
        return await self._token(
            server,
            aina_id=aina_id,
            form={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": server.redirect_uri,
                "code_verifier": verifier,
            },
        )

    async def refresh(self, server: OAuthServer, *, aina_id: str, tokens: OAuthTokens) -> OAuthTokens:
        if not tokens.refresh_token:
            raise oauth_failure("The access token expired and the server gave no refresh token; connect again")
        refreshed = await self._token(
            server, aina_id=aina_id, form={"grant_type": "refresh_token", "refresh_token": tokens.refresh_token}
        )
        # A server may keep the refresh token and omit it from the response.
        return (
            refreshed
            if refreshed.refresh_token
            else refreshed.model_copy(update={"refresh_token": tokens.refresh_token})
        )

    async def _token(self, server: OAuthServer, *, aina_id: str, form: dict[str, str]) -> OAuthTokens:
        body = {**form, "client_id": server.client_id, "resource": server.resource}
        options: dict[str, Any] = {}
        if server.client_secret:
            # A confidential client authenticates with HTTP Basic (client_secret_basic).
            options["auth"] = (server.client_id, self.box.open(server.client_secret, context=_client_context(aina_id)))
        self.require_destination(server.token_endpoint)
        try:
            response = await self.http.post(
                server.token_endpoint, data=body, headers={"Accept": "application/json"}, **options
            )
        except httpx.HTTPError as exc:
            raise oauth_failure(f"The authorization server is unreachable: {exc}") from exc
        payload = _json(response)
        if response.status_code >= 400 or not isinstance(payload.get("access_token"), str):
            reason = payload.get("error_description") or payload.get("error") or f"HTTP {response.status_code}"
            raise oauth_failure(f"The authorization server refused the token request: {reason}")
        if str(payload.get("token_type", "bearer")).lower() != "bearer":
            raise oauth_failure("The authorization server issued a token Unibot cannot send (not a bearer token)")
        expires_in = payload.get("expires_in")
        return OAuthTokens(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            expires_at=utc_now() + timedelta(seconds=int(expires_in)) if isinstance(expires_in, int | float) else None,
            scope=payload.get("scope"),
        )

    # --- sealed tokens ------------------------------------------------------------------------------------------

    def seal_tokens(self, tokens: OAuthTokens, *, context: str) -> str:
        return self.box.seal(tokens.model_dump_json(), context=f"{context}\x1foauth")

    def open_tokens(self, sealed: str, *, context: str) -> OAuthTokens:
        return OAuthTokens.model_validate_json(self.box.open(sealed, context=f"{context}\x1foauth"))

    # --- http ---------------------------------------------------------------------------------------------------

    async def _get_json(self, url: str) -> dict[str, Any] | None:
        self.require_destination(url)
        try:
            response = await self.http.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        payload = _json(response)
        return payload or None

    async def _post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        self.require_destination(url)
        try:
            response = await self.http.post(url, json=body, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            raise oauth_failure(f"The authorization server is unreachable: {exc}") from exc
        payload = _json(response)
        if response.status_code >= 400:
            reason = payload.get("error_description") or payload.get("error") or f"HTTP {response.status_code}"
            raise oauth_failure(f"The authorization server refused to register Unibot: {reason}")
        return payload


_STATE_CONTEXT = "aina-oauth-state"


def _client_context(aina_id: str) -> str:
    return f"aina-oauth-client\x1f{aina_id}"


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except (ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _url(metadata: dict[str, Any], key: str, *, browser: bool = False) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise oauth_failure(f"The authorization server's metadata has no {key}")
    parts = urlsplit(value)
    loopback = parts.hostname in {"127.0.0.1", "localhost", "::1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and loopback):
        raise oauth_failure(f"The authorization server's {key} must use HTTPS")
    if browser and (parts.username or parts.password):
        raise oauth_failure(f"The authorization server's {key} is not a plain URL")
    return value
