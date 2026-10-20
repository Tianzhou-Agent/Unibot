"""Proof that a call came from your Superbot host (design §8): the context token, checked against the host's JWKS.

An MCP server whose auth is ``none`` can be called by anyone who knows its URL. ``ContextVerifier`` checks the
ES256 context token Superbot puts in every call: its signature (the host's published key), issuer, audience (your
AINA id) and expiry, and that the call's context says the same thing as the token.

    verifier = ContextVerifier(issuer="https://unibot.example.com/api/aina-host/v1", aina_id="com.example.notes")

    @mcp.tool()
    async def list_notes(ctx: Context) -> Notes:
        unibot = await verifier.verify(ctx)   # raises ContextError for anything not signed by your host
        ...

A URL canvas's own backend checks an identity token from ``getIdentityToken()`` with ``verify_identity_token``.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any

import httpx
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

from unibot_aina.context import UnibotContext, unibot_context

HOST_API_SUFFIX = "/aina-host/v1"
JWKS_PATH = "/.well-known/aina-host-jwks.json"
# An unknown key id refetches the JWKS (the host rotated its key), at most this often.
REFETCH_SECONDS = 60.0


class ContextError(Exception):
    """The call or token was not issued by the expected Superbot host for this AINA."""


class ContextVerifier:
    def __init__(
        self,
        *,
        issuer: str,
        aina_id: str,
        jwks_url: str | None = None,
        leeway_seconds: int = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """``issuer`` is your Superbot host's Host API base, e.g. ``https://unibot.example.com/api/aina-host/v1``."""
        self.issuer = issuer.rstrip("/")
        self.aina_id = aina_id
        self.jwks_url = jwks_url or f"{self.issuer.removesuffix(HOST_API_SUFFIX)}{JWKS_PATH}"
        self.leeway = leeway_seconds
        self._transport = transport
        self._keys: dict[str, ec.EllipticCurvePublicKey] = {}
        self._fetched_at = 0.0

    async def verify(self, ctx: Any) -> UnibotContext:
        """The context of a tool call, after checking its token; ``ctx`` is the MCP SDK ``Context`` or the ``_meta``."""
        context = unibot_context(ctx)
        if not context.token:
            raise ContextError("The call carries no context token")
        claims = await self._claims(context.token)
        if claims.get("use") == "identity":
            raise ContextError("An identity token cannot authorize a tool call")
        expected = {
            "sub": context.user_id,
            "installation_id": context.installation_id,
            "invocation_id": context.invocation_id,
        }
        for claim, value in expected.items():
            if claims.get(claim) != value:
                raise ContextError(f"The context's {claim} does not match its token")
        if context.api_base is not None and context.api_base.rstrip("/") != self.issuer:
            raise ContextError("The context names another Host API than the token's issuer")
        return context

    async def verify_identity_token(self, token: str) -> dict[str, Any]:
        """The claims of a URL canvas's ``getIdentityToken()`` token: ``sub`` is the pairwise user id."""
        claims = await self._claims(token)
        if claims.get("use") != "identity":
            raise ContextError("Not an identity token")
        return claims

    async def _claims(self, token: str) -> dict[str, Any]:
        try:
            header_part, payload_part, signature_part = token.split(".")
            header = json.loads(_b64decode(header_part))
            claims = json.loads(_b64decode(payload_part))
            signature = _b64decode(signature_part)
        except (ValueError, json.JSONDecodeError) as exc:
            raise ContextError("Malformed token") from exc
        if header.get("alg") != "ES256" or len(signature) != 64:
            raise ContextError("The token is not ES256")
        key = await self._key(str(header.get("kid") or ""))
        der = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
        try:
            key.verify(der, f"{header_part}.{payload_part}".encode("ascii"), ec.ECDSA(hashes.SHA256()))
        except InvalidSignature as exc:
            raise ContextError("The token's signature is not the host's") from exc
        now = time.time()
        if claims.get("iss") != self.issuer:
            raise ContextError("The token was issued by another host")
        if claims.get("aud") != self.aina_id:
            raise ContextError("The token is for another AINA")
        if not isinstance(claims.get("exp"), int) or claims["exp"] + self.leeway < now:
            raise ContextError("The token has expired")
        return dict(claims)

    async def _key(self, kid: str) -> ec.EllipticCurvePublicKey:
        if not self._keys or (kid not in self._keys and time.monotonic() - self._fetched_at > REFETCH_SECONDS):
            await self._fetch()
        key = self._keys.get(kid)
        if key is None:
            raise ContextError("The token was signed with a key the host does not publish")
        return key

    async def _fetch(self) -> None:
        async with httpx.AsyncClient(transport=self._transport, timeout=10) as client:
            response = await client.get(self.jwks_url)
        response.raise_for_status()
        keys: dict[str, ec.EllipticCurvePublicKey] = {}
        for jwk in response.json().get("keys", []):
            if jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
                continue
            numbers = ec.EllipticCurvePublicNumbers(
                int.from_bytes(_b64decode(jwk["x"]), "big"), int.from_bytes(_b64decode(jwk["y"]), "big"), ec.SECP256R1()
            )
            keys[str(jwk.get("kid") or "")] = numbers.public_key()
        self._keys = keys
        self._fetched_at = time.monotonic()


def _b64decode(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
