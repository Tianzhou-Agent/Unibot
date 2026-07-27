from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tests.support.fake_llm import ScriptedLLM


def _settings() -> AgentSettings:
    return AgentSettings(
        _env_file=None,
        vision_base_url="http://vision.invalid",
        vision_max_image_bytes=1024,
    )


def test_image_recognition_aina_and_detection_proxy() -> None:
    async def vision_service(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(
                200,
                json={
                    "status": "ready",
                    "model": "yolo26m.pt",
                    "device": "cuda:0",
                    "requested_device": "auto",
                    "gpu_name": "Test GPU",
                },
            )
        if request.url.path == "/v1/detect":
            assert b'name="image"' in request.content
            assert b"image/png" in request.content
            return httpx.Response(
                200,
                json={
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
                },
            )
        return httpx.Response(404)

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(vision_service))
    app = create_app(
        settings=_settings(),
        llm=ScriptedLLM([]),
        vision_http_client=http_client,
    )
    with TestClient(app) as client:
        aina = client.get("/ainas/unibot-image-recognition")
        health = client.get("/vision/health")
        result = client.post(
            "/vision/detect",
            files={"image": ("sample.png", b"\x89PNG\r\n\x1a\ncontent", "image/png")},
            data={"confidence": "0.3"},
        )

    assert aina.status_code == 200
    assert aina.json()["manifest"]["main_widget"]["id"] == "unibot-image-recognition-main"
    assert health.json()["device"] == "cuda:0"
    assert result.status_code == 200
    assert result.json()["summary"] == {"人": 1}


def test_image_detection_rejects_invalid_type_and_size() -> None:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    with TestClient(
        create_app(
            settings=_settings(),
            llm=ScriptedLLM([]),
            vision_http_client=http_client,
        )
    ) as client:
        invalid_type = client.post(
            "/vision/detect",
            files={"image": ("sample.gif", b"GIF89a", "image/gif")},
        )
        too_large = client.post(
            "/vision/detect",
            files={"image": ("sample.png", b"x" * 1025, "image/png")},
        )

    assert invalid_type.status_code == 415
    assert too_large.status_code == 413
