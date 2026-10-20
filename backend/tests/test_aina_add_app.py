"""The Add app page's backend: build a package from its parts, preview its canvas, list a server's tools."""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.fake_llm import ScriptedLLM
from tests.test_aina_package import MCP_URL, _example_files, _example_server, _settings
from tianzhou_agent_platform.aina.package.mcp_client import McpToolClient
from tianzhou_agent_platform.aina.project import build_project_archive
from tianzhou_agent_platform.api.aina_add_app import _embedding
from tianzhou_agent_platform.main import create_app

SKILL = b"---\nname: draw-diagram\ndescription: Use when the user wants a diagram drawn.\n---\n# Draw\n\n1. Draw it.\n"


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def client() -> Any:
    app = create_app(
        settings=_settings(), llm=ScriptedLLM([]), mcp_client=McpToolClient(servers={MCP_URL: _example_server().mcp})
    )
    with TestClient(app) as test_client:
        yield test_client


def _manifest(**parts: Any) -> dict[str, Any]:
    return {
        "protocol_version": "2.0",
        "aina": {
            "id": "com.example.board",
            "version": "0.1.0",
            "name": "Board",
            "description": "Draw diagrams on a whiteboard.",
            "publisher": {"id": "example", "name": "Example"},
        },
        **parts,
    }


def _compose(client: TestClient, manifest: dict[str, Any], mode: str = "check", **files: Any) -> httpx.Response:
    return client.post(
        "/ainas/packages/compose",
        params={"mode": mode},
        data={"manifest": json.dumps(manifest)},
        files=[(key.replace("__", ":"), value) for key, value in files.items()],
    )


def test_an_app_built_from_parts_is_checked_previewed_registered_and_downloaded(client: TestClient) -> None:
    manifest = _manifest(
        tools={"mcp": {"url": MCP_URL, "auth": {"type": "none"}}, "expose": {"list_expenses": {"effect": "read"}}},
        skills=[{"id": "draw-diagram", "tools": ["list_expenses"]}],
        canvas={"title": "Board"},
    )
    parts = {
        "skill__draw-diagram": ("SKILL.md", SKILL, "text/markdown"),
        "canvas_bundle": (
            "board.zip",
            _zip({"board/index.html": b"<h1>Board</h1>", "board/app.js": b"1"}),
            "application/zip",
        ),
    }

    checked = _compose(client, manifest, **parts).json()
    preview = client.get(checked["canvas_preview"])
    hidden = client.get(checked["canvas_preview"].replace("canvas/index.html", "skills/draw-diagram/SKILL.md"))
    registered = _compose(client, manifest, "register", **parts)
    downloaded = _compose(client, manifest, "download", **parts)

    assert not checked["report"]["errors"], checked["report"]["errors"]
    assert preview.status_code == 200 and preview.text == "<h1>Board</h1>"
    assert "sandbox" in preview.headers["content-security-policy"]
    assert hidden.status_code == 404  # skills and the manifest are never served
    assert registered.status_code == 201, registered.text
    skill = registered.json()["manifest"]["runtime"]["package"]["skills"][0]
    assert skill == {
        "id": "draw-diagram",
        "name": "draw-diagram",
        "description": "Use when the user wants a diagram drawn.",
        "path": "skills/draw-diagram",
        "tools": ["list_expenses"],
    }
    with zipfile.ZipFile(io.BytesIO(downloaded.content)) as archive:
        assert sorted(archive.namelist()) == [
            "aina.json",
            "canvas/app.js",
            "canvas/index.html",
            "skills/draw-diagram/SKILL.md",
        ]


def test_a_skill_folder_zip_keeps_its_references(client: TestClient) -> None:
    folder = _zip({"draw/SKILL.md": SKILL, "draw/references/colors.md": b"Blue"})
    checked = _compose(
        client,
        _manifest(skills=[{"id": "draw-diagram"}]),
        "download",
        **{"skill__draw-diagram": ("draw.zip", folder, "application/zip")},
    )

    with zipfile.ZipFile(io.BytesIO(checked.content)) as archive:
        assert "skills/draw-diagram/references/colors.md" in archive.namelist()


def test_starting_from_a_package_keeps_its_other_files(client: TestClient) -> None:
    files = _example_files()
    manifest = json.loads(files["aina.json"])
    manifest["aina"]["description"] = "Track spending. Use when the user mentions money they spent."

    checked = client.post(
        "/ainas/packages/compose",
        params={"mode": "check"},
        data={"manifest": json.dumps(manifest)},
        files=[("base", ("expenses.aina.zip", build_project_archive(files), "application/zip"))],
    ).json()

    assert not checked["report"]["errors"], checked["report"]["errors"]
    assert checked["report"]["manifest"]["aina"]["description"].startswith("Track spending")
    assert checked["canvas_preview"].endswith("/canvas/index.html")


def test_a_registered_package_is_edited_as_its_next_version(client: TestClient) -> None:
    manifest = _manifest(skills=[{"id": "draw-diagram"}])
    registered = _compose(client, manifest, "register", **{"skill__draw-diagram": ("SKILL.md", SKILL, "text/markdown")})
    package = client.get("/ainas/com.example.board/package")
    edited = {**manifest, "aina": {**manifest["aina"], "version": "0.1.1", "name": "Whiteboard"}}
    updated = _compose(
        client, edited, "register", base=("com.example.board.aina.zip", package.content, "application/zip")
    )

    assert registered.status_code == 201, registered.text
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(io.BytesIO(package.content)) as archive:
        assert "skills/draw-diagram/SKILL.md" in archive.namelist()
    assert updated.status_code == 201, updated.text
    assert updated.json()["manifest"]["aina"]["name"] == "Whiteboard"
    assert updated.json()["manifest"]["runtime"]["package"]["skills"][0]["id"] == "draw-diagram"
    assert client.get("/ainas/unknown.app/package").status_code == 404


def test_a_bundle_without_index_html_is_refused(client: TestClient) -> None:
    refused = _compose(
        client,
        _manifest(canvas={"title": "Board"}),
        canvas_bundle=("b.zip", _zip({"a/b/app.js": b"1"}), "application/zip"),
    )

    assert refused.status_code == 422 and "index.html" in refused.text


def test_inspecting_a_server_lists_its_tools_with_their_implied_effect(client: TestClient) -> None:
    inspected = client.post("/ainas/mcp/inspect", json={"url": MCP_URL}).json()
    effects = {tool["name"]: tool["effect"] for tool in inspected["tools"]}

    assert effects["list_expenses"] == "read"
    assert effects["delete_expense"] == "destructive"
    floors = {tool["name"]: tool["min_effect"] for tool in inspected["tools"]}
    assert floors["delete_expense"] == "destructive" and floors["add_expense"] == "write"
    assert effects["sync_bank"] == "write" and floors["sync_bank"] == "read"  # no hints: suggested, not forced
    assert all(tool["description"] for tool in inspected["tools"])


@pytest.mark.parametrize(
    ("headers", "status", "embeddable"),
    [
        ({}, 200, True),
        ({"X-Frame-Options": "DENY"}, 200, False),
        ({"Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'"}, 200, False),
        ({}, 404, None),
    ],
)
def test_a_canvas_page_that_refuses_frames_is_reported(
    headers: dict[str, str], status: int, embeddable: bool | None
) -> None:
    assert _embedding(httpx.Response(status, headers=headers))["embeddable"] is embeddable


def test_the_bridge_sdk_is_served_for_any_canvas_to_load(client: TestClient) -> None:
    script = client.get("/aina-ui/sdk/aina-ui.js")
    styles = client.get("/aina-ui/sdk/aina-ui.css")
    other = client.get("/aina-ui/sdk/aina.json")

    assert script.status_code == 200 and "export const host" in script.text
    assert script.headers["content-type"].startswith("text/javascript")
    assert script.headers["access-control-allow-origin"] == "*"
    assert styles.status_code == 200 and styles.headers["content-type"].startswith("text/css")
    assert other.status_code == 404
