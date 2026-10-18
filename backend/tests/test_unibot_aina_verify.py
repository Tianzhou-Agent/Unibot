"""``unibot_aina.ContextVerifier``: an AINA's MCP server proves a call came from its Unibot host (design §8)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.test_aina_host_services import _install, host  # noqa: F401 - the fixture
from tests.test_aina_host_api import AINA_ID
from tianzhou_agent_platform.aina.package.context import unibot_context as host_context
from tianzhou_agent_platform.aina.package.secrets import aina_key_material
from tianzhou_agent_platform.aina.package.service import package_runtime
from tianzhou_agent_platform.aina.package.tokens import HostReference
from unibot_aina import ContextError, ContextVerifier

ISSUER = "http://testserver/aina-host/v1"


def _meta(client: TestClient, *, aina_id: str = AINA_ID) -> dict[str, Any]:
    """The ``_meta`` Unibot sends with a tool call (``AinaPackageService.call_tool``)."""
    app = client.app
    service = app.state.aina_packages  # type: ignore[attr-defined]
    installation = _install(client, ["storage"])
    record = app.state.repository._ainas[AINA_ID]  # type: ignore[attr-defined]  # noqa: SLF001
    context = host_context(
        secret=aina_key_material(service.settings),
        manifest=package_runtime(record).package,
        installation=installation.model_copy(update={"aina_id": aina_id}),
        invocation_id="call_1",
        source="agent",
        conversation_id=None,
        workspace_id=None,
    )
    context["host"] = {"api_base": ISSUER}
    context["token"] = service.mint_token(
        installation.model_copy(update={"aina_id": aina_id}), context, conversation_id=None, workspace_id=None
    )
    return {"unibot/context": context}


def _verifier(client: TestClient, **overrides: Any) -> ContextVerifier:
    options: dict[str, Any] = {"issuer": ISSUER, "aina_id": AINA_ID}
    options.update(overrides)
    return ContextVerifier(transport=httpx.ASGITransport(app=client.app), **options)


def _run(client: TestClient, coroutine: Any) -> Any:
    return client.portal.call(lambda: coroutine)  # type: ignore[union-attr]


def test_a_call_signed_by_the_host_is_accepted(host: Any) -> None:  # noqa: F811
    client, _app, _llm = host
    meta = _meta(client)

    context = _run(client, _verifier(client).verify(meta))

    assert context.user_id == meta["unibot/context"]["user"]["id"] and context.host.api_base == ISSUER


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda context: context["user"].update(id="pu_someone_else"), "sub"),
        (lambda context: context.update(installation_id="inst_other"), "installation_id"),
        (lambda context: context.update(host={"api_base": "https://evil.example/aina-host/v1"}), "Host API"),
        (lambda context: context.update(token=context["token"][:-4] + "AAAA"), "signature"),
        (lambda context: context.pop("token"), "no context token"),
    ],
)
def test_a_tampered_call_is_refused(host: Any, change: Any, message: str) -> None:  # noqa: F811
    client, _app, _llm = host
    meta = _meta(client)
    change(meta["unibot/context"])

    with pytest.raises(ContextError, match=message):
        _run(client, _verifier(client).verify(meta))


def test_a_token_for_another_aina_issuer_or_time_is_refused(host: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    client, _app, _llm = host
    meta = _meta(client)

    with pytest.raises(ContextError, match="another AINA"):
        _run(client, _verifier(client, aina_id="com.example.other").verify(meta))
    with pytest.raises(ContextError, match="another host"):
        _run(
            client,
            _verifier(
                client,
                issuer="https://other.example/aina-host/v1",
                jwks_url=f"{ISSUER.removesuffix('/aina-host/v1')}/.well-known/aina-host-jwks.json",
            ).verify(meta),
        )
    import unibot_aina.verify as verify_module

    real_time = verify_module.time.time
    monkeypatch.setattr(verify_module.time, "time", lambda: real_time() + 3600)
    with pytest.raises(ContextError, match="expired"):
        _run(client, _verifier(client).verify(meta))


def test_identity_tokens_prove_the_user_but_never_authorize_a_tool_call(host: Any) -> None:  # noqa: F811
    client, app, _llm = host
    # What ``identity_token`` mints for a URL canvas (packaged canvases cannot ask for one).
    token = app.state.aina_packages.tokens.mint(
        reference=HostReference(tenant_id="t1", user_id="alice", aina_id=AINA_ID),
        claims={"use": "identity", "sub": "pu_alice", "scopes": []},
    )
    meta = _meta(client)
    meta["unibot/context"]["token"] = token

    claims = _run(client, _verifier(client).verify_identity_token(token))
    with pytest.raises(ContextError, match="identity token"):
        _run(client, _verifier(client).verify(meta))
    with pytest.raises(ContextError, match="Not an identity token"):
        _run(client, _verifier(client).verify_identity_token(_meta(client)["unibot/context"]["token"]))

    assert claims["use"] == "identity" and claims["sub"] == "pu_alice"
