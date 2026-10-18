"""Encryption of AINA secrets at rest: ``x-secret`` configuration values and host references in context tokens.

AES-256-GCM with a key derived from ``UNIBOT_AINA_SECRET_KEY`` (or, when unset, the auth secret). Every ciphertext
is bound to a context string (for example the installation it belongs to), so a value copied into another record
does not decrypt.
"""

from __future__ import annotations

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError

_PREFIX = "v1:"


def derive_key(material: str, purpose: str, length: int = 32) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=None, info=purpose.encode("utf-8")).derive(
        material.encode("utf-8")
    )


def aina_key_material(settings: AgentSettings) -> str:
    configured = settings.aina_secret_key
    return (configured or settings.auth_secret).get_secret_value()


class SecretBox:
    def __init__(self, material: str) -> None:
        self._aead = AESGCM(derive_key(material, "unibot-aina-secrets-v1"))

    @classmethod
    def from_settings(cls, settings: AgentSettings) -> SecretBox:
        return cls(aina_key_material(settings))

    def seal(self, plaintext: str, *, context: str) -> str:
        nonce = os.urandom(12)
        sealed = self._aead.encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))
        return _PREFIX + base64.urlsafe_b64encode(nonce + sealed).decode("ascii").rstrip("=")

    def open(self, token: str, *, context: str) -> str:
        if not token.startswith(_PREFIX):
            raise _undecryptable()
        raw = token[len(_PREFIX):]
        try:
            payload = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            plaintext = self._aead.decrypt(payload[:12], payload[12:], context.encode("utf-8"))
        except (ValueError, InvalidTag) as exc:
            raise _undecryptable() from exc
        return plaintext.decode("utf-8")


def _undecryptable() -> PlatformError:
    return PlatformError(
        "AUTHENTICATION_FAILED",
        "A stored AINA secret could not be decrypted; reconfigure the installation",
        status_code=500,
        source="aina_package",
    )
