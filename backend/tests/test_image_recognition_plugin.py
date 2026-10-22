"""Image Recognition is an optional, explicitly installed example AINA."""

from __future__ import annotations

import ast
import base64
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.support.fake_llm import LLMResult, ScriptedLLM, assistant
from tests.support.example_ainas import ROOT, example_files, example_server
from tests.test_aina_package import _call, _example_files, _register, _set, _settings, _with_manifest
from tianzhou_agent_platform.aina.builtin import ensure_builtin_ainas
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.aina.package.mcp_client import McpToolClient

server = example_server("image-recognition-aina")

AINA_ID = "unibot.image-recognition"
PNG = b"\x89PNG\r\n\x1a\ncontent"
DETECTION = {
    "model": "yolo26m.pt",
    "device": "cuda:0",
    "image": {"width": 640, "height": 480},
    "detections": [
        {
            "class_id": 0,
            "label": "person",
            "label_zh": "人",
            "confidence": 0.95,
            "box": {"x1": 10, "y1": 20, "x2": 200, "y2": 400},
        }
    ],
    "summary": {"人": 1},
    "inference_ms": 12.5,
}


@pytest.fixture
def vision(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[httpx.Request]]:
    """A fake YOLO vision service; each test may replace ``responses``."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/healthz":
            return httpx.Response(
                200, json={"status": "ready", "model": "yolo26m.pt", "device": "cuda:0", "requested_device": "auto"}
            )
        return httpx.Response(200, json=DETECTION)

    monkeypatch.setenv("UNIBOT_VISION_BASE_URL", "http://vision.invalid")
    monkeypatch.setenv("UNIBOT_VISION_MAX_IMAGE_BYTES", "1024")
    monkeypatch.setattr(server, "VISION_TRANSPORT", httpx.MockTransport(handle))
    yield requests


def _client(llm: ScriptedLLM | None = None) -> TestClient:
    client = TestClient(
        create_app(
            settings=_settings(),
            llm=llm or ScriptedLLM([]),
            mcp_client=McpToolClient(servers={"http://127.0.0.1:8767/mcp": server.mcp}),
        )
    )
    return client


def _install(client: TestClient) -> None:
    registered = _register(client, example_files("image-recognition-aina"))
    assert registered.status_code == 201, registered.text
    installed = client.post(f"/ainas/{AINA_ID}/install", json={"granted_permissions": []})
    assert installed.status_code == 200, installed.text


def _detect(client: TestClient, image: bytes, content_type: str = "image/png") -> httpx.Response:
    arguments = {"image_base64": base64.b64encode(image).decode(), "content_type": content_type, "filename": "a.png"}
    return client.post(f"/ainas/{AINA_ID}/app-tools/detect", json={"arguments": arguments})


def test_example_requires_registration_and_installation(vision: list[httpx.Request]) -> None:
    with _client() as client:
        assert client.get(f"/ainas/{AINA_ID}").status_code == 404
        assert _register(client, example_files("image-recognition-aina")).status_code == 201
        assert client.post(f"/ainas/{AINA_ID}/open", json={}).status_code == 404
        assert _detect(client, PNG).status_code == 403
        _install(client)
        _call(client, ensure_builtin_ainas, client.app.state.repository)
        record = client.get(f"/ainas/{AINA_ID}").json()
        opened = client.post(f"/ainas/{AINA_ID}/open", json={})
        canvas = client.get(f"/aina-ui/{AINA_ID}/2.0.0/canvas/index.html")
        status = client.post(f"/ainas/{AINA_ID}/app-tools/vision_status", json={"arguments": {}})
        installations = client.get("/installations").json()
        assert client.delete(f"/ainas/{AINA_ID}/install").status_code == 204
        assert _detect(client, PNG).status_code == 403

    runtime = record["manifest"]["runtime"]
    assert runtime["type"] == "package" and runtime["bundled"] is False
    assert {tool["name"]: tool["visibility"] for tool in runtime["tools"]} == {
        "detect": ["app"],
        "vision_status": ["app"],
    }
    assert opened.status_code == 200 and opened.json()["main_widget"]["kind"] == "aina_canvas"
    assert canvas.status_code == 200 and "./assets/" in canvas.text
    assert status.json()["structuredContent"]["device"] == "cuda:0"
    assert [item["aina_id"] for item in installations] == [AINA_ID]


def test_startup_removes_the_old_implicit_bundled_installation(vision: list[httpx.Request]) -> None:
    with _client() as client:
        _install(client)
        repository = client.app.state.repository
        record = _call(client, repository.get_aina, AINA_ID)
        record.manifest.runtime.bundled = True
        _call(client, repository.upsert_aina, record)
        _call(client, ensure_builtin_ainas, repository)
        assert client.get(f"/ainas/{AINA_ID}").status_code == 404
        assert client.get("/installations").json() == []
        assert _detect(client, PNG).status_code == 404


def test_detect_sends_the_image_to_the_vision_service(vision: list[httpx.Request]) -> None:
    with _client() as client:
        _install(client)
        response = _detect(client, PNG)

    assert response.status_code == 200, response.text
    assert response.json()["structuredContent"]["summary"] == {"人": 1}
    (request,) = vision
    assert request.url == "http://vision.invalid/v1/detect"
    assert b'name="image"' in request.content and b"image/png" in request.content and PNG in request.content


@pytest.mark.parametrize(
    ("image", "content_type", "code"),
    [
        (b"GIF89a", "image/gif", "UNSUPPORTED_TYPE"),
        (b"x" * 1025, "image/png", "TOO_LARGE"),
        (b"", "image/png", "INVALID_IMAGE"),
    ],
)
def test_detect_rejects_images_before_calling_the_service(
    vision: list[httpx.Request], image: bytes, content_type: str, code: str
) -> None:
    with _client() as client:
        _install(client)
        response = _detect(client, image, content_type)

    assert response.status_code == 502
    assert f"{code}:" in response.json()["error"]["message"]
    assert vision == []


def test_vision_service_failures_reach_the_canvas_as_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(server, "VISION_TRANSPORT", httpx.MockTransport(down))
    with _client() as client:
        _install(client)
        unavailable = _detect(client, PNG)
    monkeypatch.setattr(server, "VISION_TRANSPORT", httpx.MockTransport(lambda _: httpx.Response(422)))
    with _client() as client:
        _install(client)
        unsafe = _detect(client, PNG)

    assert "UNAVAILABLE:" in unavailable.json()["error"]["message"]
    assert "INVALID_IMAGE:" in unsafe.json()["error"]["message"]


def test_an_installed_app_only_package_is_not_offered_to_the_model(vision: list[httpx.Request]) -> None:
    def no_entry(*, tools: list[dict[str, Any]], **_: Any) -> LLMResult:
        assert not any("image" in item["function"]["name"] for item in tools)
        return assistant("ok")

    with _client(ScriptedLLM([no_entry])) as client:
        _install(client)
        assert client.post("/chat", json={"message": "hello"}).json()["status"] == "completed"


def test_only_bundled_packages_may_use_the_bundled_host() -> None:
    impostor = _with_manifest(_example_files(), _set("tools.mcp.url", f"https://bundled.unibot.invalid/{AINA_ID}/mcp"))
    with _client() as client:
        claimed = _register(client, impostor)

    assert claimed.status_code == 422


@pytest.mark.parametrize("package", ["image-recognition-aina", "code-runner-aina", "unibot_aina"])
def test_example_code_never_imports_the_platform(package: str) -> None:
    root = (Path(__file__).resolve().parents[1] / "src" / package) if package == "unibot_aina" else ROOT / package
    offenders = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            names = [alias.name for alias in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            offenders += [
                f"{path.relative_to(root)}: {name}" for name in names if name.split(".")[0] == "tianzhou_agent_platform"
            ]
    assert offenders == []
