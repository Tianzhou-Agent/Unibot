"""The Host API OpenAPI document (design §8, published at ``spec/aina/2.0/host-api.openapi.json``).

It is cut from the host's own OpenAPI, so it cannot drift from the routes; a test checks the published copy.
Regenerate with ``python -m tianzhou_agent_platform.aina.package.openapi spec/aina/2.0/host-api.openapi.json``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from tianzhou_agent_platform.api.aina_host import HOST_API_PREFIX, JWKS_PATH

DESCRIPTION = (
    "What an AINA 2.0 MCP server may ask of the Unibot host on behalf of one tool call. Every request carries the "
    "call's context token (`_meta[\"unibot/context\"].token`) as a bearer token; each route also needs one "
    "permission the user granted (`storage`, `documents.read`, `documents.write`, `memory.read`, `memory.write`, "
    "`llm`). Task callbacks and `token/refresh` need only a token of the invocation that started the task. The "
    "server URL is the call's `_meta[\"unibot/context\"].host.api_base`."
)


def host_api_openapi(app: FastAPI) -> dict[str, Any]:
    full = app.openapi()
    paths = {
        path.removeprefix(HOST_API_PREFIX) or "/": operations
        for path, operations in full["paths"].items()
        if path.startswith(f"{HOST_API_PREFIX}/")
    }
    for operations in paths.values():
        for operation in operations.values():
            operation["security"] = [{"contextToken": []}]
            operation.pop("tags", None)
    schemas = full.get("components", {}).get("schemas", {})
    used: dict[str, Any] = {}
    _collect(paths, schemas, used)
    jwks = full["paths"][JWKS_PATH]["get"]
    jwks.pop("tags", None)
    return {
        "openapi": full["openapi"],
        "info": {"title": "Unibot AINA Host API", "version": "2.0", "description": DESCRIPTION},
        "servers": [{"url": "{api_base}", "variables": {"api_base": {"default": "https://unibot.example.com/api/aina-host/v1"}}}],
        "paths": dict(sorted(paths.items())),
        "components": {
            "schemas": dict(sorted(used.items())),
            "securitySchemes": {
                "contextToken": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT (ES256)"}
            },
        },
        "x-jwks": {"path": JWKS_PATH, "description": "The host's public signing keys, served next to the API root.", "get": jwks},
    }


def _collect(value: Any, schemas: dict[str, Any], used: dict[str, Any]) -> None:
    """Every component schema ``value`` references, transitively."""
    if isinstance(value, dict):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/components/schemas/"):
            name = reference.rsplit("/", 1)[1]
            if name not in used:
                used[name] = schemas[name]
                _collect(schemas[name], schemas, used)
        for item in value.values():
            _collect(item, schemas, used)
    elif isinstance(value, list):
        for item in value:
            _collect(item, schemas, used)


def main() -> None:
    from tianzhou_agent_platform.config import AgentSettings
    from tianzhou_agent_platform.main import create_app

    document = host_api_openapi(create_app(settings=AgentSettings(_env_file=None)))  # type: ignore[call-arg]
    Path(sys.argv[1]).write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
