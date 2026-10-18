"""The published AINA 2.0 spec and SDKs (design §12) stay in step with the host."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support.fake_llm import ScriptedLLM
from tianzhou_agent_platform.aina.package.openapi import host_api_openapi
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "spec" / "aina" / "2.0"


def test_the_published_host_api_openapi_matches_the_routes() -> None:
    generated = host_api_openapi(create_app(settings=AgentSettings(_env_file=None), llm=ScriptedLLM([])))  # type: ignore[call-arg]
    published = json.loads((SPEC / "host-api.openapi.json").read_text(encoding="utf-8"))

    # Regenerate: uv run python -m tianzhou_agent_platform.aina.package.openapi ../spec/aina/2.0/host-api.openapi.json
    assert published == json.loads(json.dumps(generated))
    assert "/storage/kv/{key}" in published["paths"] and "/llm/chat" in published["paths"]


def test_the_sdk_packages_ship_the_host_side_sources() -> None:
    python_sdk = REPO / "sdk" / "python" / "src" / "unibot_aina"
    ui = json.loads((SPEC / "ui" / "package.json").read_text(encoding="utf-8"))

    assert python_sdk.resolve() == (REPO / "backend" / "src" / "unibot_aina").resolve()
    assert all((SPEC / "ui" / name).is_file() for name in ui["files"])
