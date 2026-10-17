"""Host-signed context tokens for AINA MCP servers (design §6.4, §8).

A context token is an ES256 JWT minted per invocation. Its identity claims are the pairwise ids also sent in
``_meta["unibot/context"]``; an MCP server verifies it with the host JWKS and uses it as the Host API bearer. The
``ref`` claim is the host's own reference to the installation, encrypted, so the host can act on a token without
keeping per-token state and the AINA learns nothing from it.

The signing key comes from ``UNIBOT_AINA_SIGNING_KEY`` (PEM, P-256). When unset it is derived from the AINA key
material, so every node that shares that secret signs with the same key.
"""

from __future__ import annotations

import hashlib
import json
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


@dataclass(frozen=True, slots=True)
class VerifiedToken:
    reference: HostReference
    claims: dict[str, Any]

    @property
    def scopes(self) -> set[str]:
        return {str(scope) for scope in self.claims.get("scopes") or []}


class ContextTokenSigner:
    def __init__(self, *, issuer: str, private_key: ec.EllipticCurvePrivateKey, box: SecretBox) -> None:
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
        self._public_jwk = {
            **jwk.construct(public_pem.decode("ascii"), ALGORITHM).to_dict(),
            "kid": self.kid,
            "use": "sig",
            "alg": ALGORITHM,
        }

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
        return cls(issuer=settings.aina_host_api_base, private_key=private_key, box=SecretBox.from_settings(settings))

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self._public_jwk]}

    def mint(self, *, reference: HostReference, claims: dict[str, Any], ttl_seconds: int = MAX_TOKEN_SECONDS) -> str:
        now = int(time())
        ref = self._box.seal(
            json.dumps([reference.tenant_id, reference.user_id, reference.aina_id], separators=(",", ":")),
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

    def verify(self, token: str) -> VerifiedToken:
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                self._public_jwk,
                algorithms=[ALGORITHM],
                issuer=self.issuer,
                options={"verify_aud": False, "require_exp": True, "require_iat": True},
            )
            tenant_id, user_id, aina_id = json.loads(self._box.open(str(claims["ref"]), context=_REF_CONTEXT))
        except (JWTError, KeyError, ValueError, TypeError, PlatformError) as exc:
            raise invalid_token() from exc
        if claims.get("aud") != aina_id or int(claims["exp"]) - int(claims["iat"]) > MAX_TOKEN_SECONDS:
            raise invalid_token()
        return VerifiedToken(HostReference(tenant_id=tenant_id, user_id=user_id, aina_id=aina_id), claims)


def invalid_token() -> PlatformError:
    return PlatformError(
        "AUTHENTICATION_FAILED",
        "The AINA context token is missing, invalid or expired",
        status_code=401,
        source="aina_host",
    )
