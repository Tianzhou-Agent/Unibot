from __future__ import annotations

import hashlib
import io
import zipfile
from typing import Any

import yaml
from fastapi.testclient import TestClient

from tianzhou_agent_platform.aina.project import build_project_archive
from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tests.support.fake_llm import ScriptedLLM


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        llm_base_url="https://model.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
    )


def _managed_manifest(**overrides: Any) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "protocol_version": "1.0",
        "aina": {
            "id": "com.example.managed",
            "name": "Managed AINA",
            "version": "0.1.0",
            "description": "A packaged managed AINA.",
            "publisher": {"id": "tests", "name": "Tests"},
        },
        "runtime": {
            "type": "managed",
            "language": "python",
            "entrypoint": "src/main.py:invoke",
            "dependency_file": "requirements.txt",
        },
        "capabilities": {"skills": [], "tools": [], "ui": [], "events": []},
        "permissions": [],
        "authentication": {"type": "none"},
    }
    manifest.update(overrides)
    return manifest


def _archive(manifest: dict[str, Any], **files: str) -> bytes:
    return build_project_archive(
        {
            "aina.yaml": yaml.safe_dump(manifest, sort_keys=False),
            "src/main.py": "async def invoke(request):\n    return request\n",
            "requirements.txt": "",
            **files,
        }
    )


def test_scaffold_is_deterministic_and_platform_can_validate_it() -> None:
    payload = {
        "aina_id": "com.example.scaffold",
        "name": "Scaffold AINA",
        "description": "Created from the platform project template.",
        "language": "python",
    }
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        first = client.post("/aina-projects/scaffold", json=payload)
        second = client.post("/aina-projects/scaffold", json=payload)
        validated = client.post(
            "/aina-projects/validate",
            files={"file": ("scaffold.aina.zip", first.content, "application/zip")},
        )

    assert first.status_code == 200
    assert first.headers["content-type"] == "application/zip"
    assert first.content == second.content
    with zipfile.ZipFile(io.BytesIO(first.content)) as archive:
        assert set(archive.namelist()) == {"README.md", "aina.yaml", "requirements.txt", "src/main.py"}
    report = validated.json()
    assert validated.status_code == 200
    assert report["format_version"] == "1.0"
    assert report["archive_sha256"] == hashlib.sha256(first.content).hexdigest()
    assert report["manifest"]["aina"]["id"] == "com.example.scaffold"
    assert report["manifest"]["runtime"]["type"] == "managed"
    assert report["ready_for_registration"] is False
    assert "require deployment" in report["warnings"][0]


def test_node_scaffold_uses_node_entrypoint_and_package_metadata() -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        response = client.post(
            "/aina-projects/scaffold",
            json={
                "aina_id": "com.example.node",
                "name": "Node AINA",
                "description": "Node project",
                "language": "node",
            },
        )
        validated = client.post(
            "/aina-projects/validate",
            files={"file": ("node.aina.zip", response.content, "application/zip")},
        )

    assert response.status_code == 200
    assert validated.status_code == 200
    assert validated.json()["manifest"]["runtime"] == {
        "type": "managed",
        "language": "node",
        "entrypoint": "src/index.mjs:invoke",
        "dependency_file": "package.json",
    }


def test_project_validation_rejects_missing_entrypoint_and_invalid_schema() -> None:
    missing_entrypoint = build_project_archive(
        {
            "aina.yaml": yaml.safe_dump(_managed_manifest(), sort_keys=False),
            "requirements.txt": "",
        }
    )
    invalid_manifest = _managed_manifest()
    invalid_manifest["capabilities"]["tools"] = [
        {
            "id": "bad.tool",
            "name": "Bad tool",
            "description": "Invalid schema",
            "input_schema": {"type": "string", "minLength": -1},
        }
    ]
    invalid_schema = _archive(invalid_manifest)

    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        missing = client.post(
            "/aina-projects/validate",
            files={"file": ("missing.zip", missing_entrypoint, "application/zip")},
        )
        invalid = client.post(
            "/aina-projects/validate",
            files={"file": ("invalid.zip", invalid_schema, "application/zip")},
        )

    assert missing.status_code == 422
    assert "entrypoint 'src/main.py' is missing" in missing.json()["error"]["message"]
    assert invalid.status_code == 422
    assert "minLength must be a non-negative integer" in invalid.json()["error"]["message"]


def test_project_validation_rejects_archive_path_traversal() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, mode="w") as archive:
        archive.writestr("../aina.yaml", yaml.safe_dump(_managed_manifest(), sort_keys=False))

    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        response = client.post(
            "/aina-projects/validate",
            files={"file": ("unsafe.zip", buffer.getvalue(), "application/zip")},
        )

    assert response.status_code == 422
    assert response.json()["error"]["source"] == "aina_project"
    assert "escapes the project root" in response.json()["error"]["message"]


def test_managed_project_manifest_cannot_be_registered_before_deployment() -> None:
    with TestClient(create_app(settings=_settings(), llm=ScriptedLLM([]))) as client:
        response = client.post("/ainas", json=_managed_manifest())

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"
    assert "deployed remote" in response.json()["error"]["message"]
