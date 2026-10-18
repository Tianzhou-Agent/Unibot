"""Rotating the context-token signing key: tokens signed with a key being rotated out keep working until it is gone."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from pydantic import SecretStr

from tests.support.fake_llm import ScriptedLLM
from tianzhou_agent_platform.aina.package.tokens import ContextTokenSigner, HostReference, previous_verification_keys
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.main import create_app
from unibot_aina import ContextVerifier

ISSUER = "http://testserver/aina-host/v1"
REFERENCE = HostReference(tenant_id="t1", user_id="alice", aina_id="com.example.notes")


def _pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def _settings(**overrides: Any) -> AgentSettings:
    return AgentSettings(  # type: ignore[call-arg]
        _env_file=None,
        aina_host_api_base=ISSUER,
        aina_secret_key=SecretStr("aina-secret-key-shared-by-all-nodes-0123456789"),
        **overrides,
    )


def _token(signer: ContextTokenSigner) -> str:
    return signer.mint(reference=REFERENCE, claims={"sub": "pu_alice", "scopes": []})


old_key, new_key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())


def test_a_token_signed_with_a_key_being_rotated_out_is_still_accepted() -> None:
    old = ContextTokenSigner.from_settings(_settings(aina_signing_key=SecretStr(_pem(old_key))))
    rotated = ContextTokenSigner.from_settings(
        _settings(aina_signing_key=SecretStr(_pem(new_key)), aina_previous_signing_keys=SecretStr(_pem(old_key)))
    )
    finished = ContextTokenSigner.from_settings(_settings(aina_signing_key=SecretStr(_pem(new_key))))
    token = _token(old)

    assert rotated.verify(token).reference == REFERENCE
    assert [key["kid"] for key in rotated.jwks()["keys"]] == [rotated.kid, old.kid]  # the signing key first
    assert rotated.verify(_token(rotated)).reference == REFERENCE  # new tokens use the new key
    with pytest.raises(PlatformError):
        finished.verify(token)  # once the old key is removed, its tokens stop working


def test_previous_keys_may_be_given_as_the_published_jwks() -> None:
    derived = ContextTokenSigner.from_settings(_settings())  # a key derived from the AINA key material
    rotated = ContextTokenSigner.from_settings(
        _settings(
            aina_signing_key=SecretStr(_pem(new_key)),
            aina_previous_signing_keys=SecretStr(json.dumps(derived.jwks())),
        )
    )

    assert rotated.verify(_token(derived)).reference == REFERENCE


@pytest.mark.parametrize(
    "value",
    [
        "not a key",
        json.dumps({"keys": [{"kty": "RSA", "kid": "x"}]}),
        ec.generate_private_key(ec.SECP384R1())
        .private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        .decode(),
    ],
)
def test_unusable_previous_keys_are_refused_at_startup(value: str) -> None:
    with pytest.raises(ValueError):
        previous_verification_keys(value)


def test_mcp_servers_verifying_with_the_jwks_accept_tokens_from_before_the_rotation() -> None:
    old = ContextTokenSigner.from_settings(_settings(aina_signing_key=SecretStr(_pem(old_key))))
    app = create_app(
        settings=_settings(
            aina_signing_key=SecretStr(_pem(new_key)), aina_previous_signing_keys=SecretStr(_pem(old_key))
        ),
        llm=ScriptedLLM([]),
    )
    verifier = ContextVerifier(issuer=ISSUER, aina_id=REFERENCE.aina_id, transport=httpx.ASGITransport(app=app))
    with TestClient(app) as client:
        published = client.get("/.well-known/aina-host-jwks.json").json()["keys"]
        claims = client.portal.call(
            verifier.verify_identity_token,
            old.mint(  # type: ignore[union-attr]
                reference=REFERENCE, claims={"use": "identity", "sub": "pu_alice", "scopes": []}
            ),
        )

    assert {key["kid"] for key in published} == {old.kid, app.state.aina_packages.tokens.kid}
    assert claims["sub"] == "pu_alice"
