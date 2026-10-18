"""Host-signed context tokens for AINA MCP servers (design §6.4, §8).

A context token is an ES256 JWT minted per invocation. Its identity claims are the pairwise ids also sent in
``_meta["unibot/context"]``; an MCP server verifies it with the host JWKS and uses it as the Host API bearer. The
``ref`` claim is the host's own reference to the installation, encrypted, so the host can act on a token without
keeping per-token state and the AINA learns nothing from it.

The signing key comes from ``UNIBOT_AINA_SIGNING_KEY`` (PEM, P-256). When unset it is derived from the AINA key
material, so every node that shares that secret signs with the same key.

Rotation: keys in ``UNIBOT_AINA_PREVIOUS_SIGNING_KEYS`` are no longer used to sign, but tokens they signed are still
accepted and their public keys stay in the JWKS, so MCP servers keep verifying tokens minted before the switch. Keep
an old key there for at least the token lifetime (10 minutes) plus the time MCP servers cache the JWKS.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from time import time
from typing import Any
from uuid import uuid4

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from jose import JWTError, jwk, jwt  # type: ignore[import-untyped]

from tianzhou_agent_platform.aina.package.secrets import SecretBox, aina_key_material, derive_key
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError

ALGORITHM = "ES256"
MAX_TOKEN_SECONDS = 600
_REF_CONTEXT = "unibot-aina-context-token-ref"
# Order of the P-256 group: a derived private scalar must lie in [1, n - 1].
_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


@dataclass(frozen=True, slots=True)
class HostReference:
    """Raw identifiers of the installation a token was minted for; never leaves the host unencrypted."""

    tenant_id: str
    user_id: str
    aina_id: str
    # The invocation's conversation and workspace: documents are read in that workspace (design §8).
    conversation_id: str | None = None
    workspace_id: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    reference: HostReference
    claims: dict[str, Any]

    @property
    def scopes(self) -> set[str]:
        return {str(scope) for scope in self.claims.get("scopes") or []}


class ContextTokenSigner:
    def __init__(
        self,
        *,
        issuer: str,
        private_key: ec.EllipticCurvePrivateKey,
        box: SecretBox,
        previous_keys: list[dict[str, Any]] | None = None,
    ) -> None:
        self.issuer = issuer
        self._box = box
        self._private_pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode("ascii")
        public_pem = private_key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.kid = hashlib.sha256(public_pem).hexdigest()[:16]
        self._public_jwk = _public_jwk(public_pem.decode("ascii"))
        # Every key a token may be verified with, by key id: the current one first, then those being rotated out.
        self._verification_keys = {self.kid: self._public_jwk}
        for previous in previous_keys or []:
            self._verification_keys.setdefault(str(previous["kid"]), previous)

    @classmethod
    def from_settings(cls, settings: AgentSettings) -> ContextTokenSigner:
        configured = settings.aina_signing_key
        if configured is not None:
            loaded = serialization.load_pem_private_key(configured.get_secret_value().encode("utf-8"), None)
            if not isinstance(loaded, ec.EllipticCurvePrivateKey) or loaded.curve.name != "secp256r1":
                raise ValueError("UNIBOT_AINA_SIGNING_KEY must be a P-256 EC private key in PEM form")
            private_key = loaded
        else:
            seed = int.from_bytes(derive_key(aina_key_material(settings), "unibot-aina-signing-key-v1", 48), "big")
            private_key = ec.derive_private_key(seed % (_P256_ORDER - 1) + 1, ec.SECP256R1())
        previous = settings.aina_previous_signing_keys
        return cls(
            issuer=settings.aina_host_api_base,
            private_key=private_key,
            box=SecretBox.from_settings(settings),
            previous_keys=previous_verification_keys(previous.get_secret_value()) if previous is not None else [],
        )

    def jwks(self) -> dict[str, Any]:
        return {"keys": list(self._verification_keys.values())}

    def mint(self, *, reference: HostReference, claims: dict[str, Any], ttl_seconds: int = MAX_TOKEN_SECONDS) -> str:
        now = int(time())
        ref = self._box.seal(
            json.dumps(
                [
                    reference.tenant_id,
                    reference.user_id,
                    reference.aina_id,
                    reference.conversation_id,
                    reference.workspace_id,
                ],
                separators=(",", ":"),
            ),
            context=_REF_CONTEXT,
        )
        payload = {
            **claims,
            "iss": self.issuer,
            "aud": reference.aina_id,
            "iat": now,
            "exp": now + min(ttl_seconds, MAX_TOKEN_SECONDS),
            "jti": uuid4().hex,
            "ref": ref,
        }
        return str(jwt.encode(payload, self._private_pem, algorithm=ALGORITHM, headers={"kid": self.kid}))

    def verify(self, token: str, *, allow_expired: bool = False) -> VerifiedToken:
        """``allow_expired`` accepts a correctly signed token past its ``exp``; only token refresh uses it."""
        try:
            key = self._verification_keys.get(str(jwt.get_unverified_header(token).get("kid") or self.kid))
            if key is None:
                raise invalid_token()
            claims: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                issuer=self.issuer,
                # python-jose turns verify_exp back on for a required exp; its presence is checked below.
                options={
                    "verify_aud": False,
                    "verify_exp": not allow_expired,
                    "require_exp": not allow_expired,
                    "require_iat": True,
                },
            )
            tenant_id, user_id, aina_id, conversation_id, workspace_id = json.loads(
                self._box.open(str(claims["ref"]), context=_REF_CONTEXT)
            )
        except (JWTError, KeyError, ValueError, TypeError, PlatformError) as exc:
            raise invalid_token() from exc
        if claims.get("aud") != aina_id or int(claims["exp"]) - int(claims["iat"]) > MAX_TOKEN_SECONDS:
            raise invalid_token()
        reference = HostReference(
            tenant_id=tenant_id,
            user_id=user_id,
            aina_id=aina_id,
            conversation_id=conversation_id,
            workspace_id=workspace_id,
        )
        return VerifiedToken(reference, claims)


def _public_jwk(public_pem: str) -> dict[str, Any]:
    kid = hashlib.sha256(public_pem.encode("ascii")).hexdigest()[:16]
    return {**jwk.construct(public_pem, ALGORITHM).to_dict(), "kid": kid, "use": "sig", "alg": ALGORITHM}


def previous_verification_keys(value: str) -> list[dict[str, Any]]:
    """Keys being rotated out: PEM keys (private or public, concatenated) or the host's JWKS JSON as published."""
    value = value.strip()
    if not value:
        return []
    if value.startswith("{"):
        keys = json.loads(value).get("keys") or []
        if not all(key.get("kty") == "EC" and key.get("crv") == "P-256" and key.get("kid") for key in keys):
            raise ValueError("UNIBOT_AINA_PREVIOUS_SIGNING_KEYS must hold P-256 EC keys with a kid")
        return [{**key, "use": "sig", "alg": ALGORITHM} for key in keys]
    found: list[dict[str, Any]] = []
    for block in re.findall(r"-----BEGIN [A-Z ]+-----.+?-----END [A-Z ]+-----", value, flags=re.DOTALL):
        data = block.encode("ascii")
        loaded: Any = (
            serialization.load_pem_private_key(data, None).public_key()
            if "PRIVATE KEY" in block
            else serialization.load_pem_public_key(data)
        )
        if not isinstance(loaded, ec.EllipticCurvePublicKey) or loaded.curve.name != "secp256r1":
            raise ValueError("UNIBOT_AINA_PREVIOUS_SIGNING_KEYS must hold P-256 EC keys")
        pem = loaded.public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        found.append(_public_jwk(pem.decode("ascii")))
    if not found:
        raise ValueError("UNIBOT_AINA_PREVIOUS_SIGNING_KEYS holds no PEM key or JWKS")
    return found


def invalid_token() -> PlatformError:
    return PlatformError(
        "AUTHENTICATION_FAILED",
        "The AINA context token is missing, invalid or expired",
        status_code=401,
        source="aina_host",
    )
