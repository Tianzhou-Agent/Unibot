from __future__ import annotations

import sys
from io import BytesIO
from types import ModuleType

from fastapi.testclient import TestClient
from PIL import Image

from app import (
    BoundingBox,
    Detection,
    DetectionResponse,
    HealthResponse,
    ImageMetadata,
    VisionSettings,
    YoloEngine,
    create_app,
)


class FakeEngine:
    async def load(self) -> None:
        return None

    def health(self) -> HealthResponse:
        return HealthResponse(
            status="ready",
            model="yolo26m.pt",
            device="cuda:0",
            requested_device="auto",
            gpu_name="Test GPU",
        )

    async def detect(self, image: Image.Image, confidence: float) -> DetectionResponse:
        assert confidence == 0.3
        return DetectionResponse(
            model="yolo26m.pt",
            device="cuda:0",
            image=ImageMetadata(width=image.width, height=image.height),
            detections=[
                Detection(
                    class_id=0,
                    label="person",
                    label_zh="人",
                    confidence=0.95,
                    box=BoundingBox(x1=1, y1=2, x2=10, y2=20),
                )
            ],
            summary={"人": 1},
            inference_ms=12.5,
        )


def _png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (32, 24), color="white").save(output, format="PNG")
    return output.getvalue()


def test_health_and_detection_contract() -> None:
    settings = VisionSettings(
        model_path="yolo26m.pt",
        requested_device="auto",
        image_size=640,
        max_image_bytes=1024 * 1024,
        max_image_pixels=1_000_000,
        max_concurrency=1,
    )
    with TestClient(create_app(settings=settings, engine=FakeEngine())) as client:
        health = client.get("/healthz")
        detection = client.post(
            "/v1/detect",
            files={"image": ("test.png", _png(), "image/png")},
            data={"confidence": "0.3"},
        )

    assert health.json()["device"] == "cuda:0"
    assert detection.status_code == 200
    assert detection.json()["summary"] == {"人": 1}
    assert detection.json()["image"] == {"width": 32, "height": 24}


def test_detection_rejects_unsupported_content_type() -> None:
    with TestClient(create_app(engine=FakeEngine())) as client:
        response = client.post(
            "/v1/detect",
            files={"image": ("test.gif", b"GIF89a", "image/gif")},
        )

    assert response.status_code == 415


def test_auto_device_prefers_gpu(monkeypatch) -> None:
    engine, moved_devices = _engine_with_fake_runtime(monkeypatch, cuda_available=True)

    engine._load_sync()

    assert engine.device == "cuda:0"
    assert engine.gpu_name == "Test GPU"
    assert moved_devices == ["cuda:0"]


def test_cuda_request_falls_back_to_cpu(monkeypatch) -> None:
    engine, moved_devices = _engine_with_fake_runtime(
        monkeypatch,
        cuda_available=False,
        requested_device="cuda:0",
    )

    engine._load_sync()

    assert engine.device == "cpu"
    assert engine.gpu_name is None
    assert moved_devices == ["cpu"]


def _engine_with_fake_runtime(
    monkeypatch,
    *,
    cuda_available: bool,
    requested_device: str = "auto",
) -> tuple[YoloEngine, list[str]]:
    moved_devices: list[str] = []

    class FakeModel:
        def to(self, device: str) -> None:
            moved_devices.append(device)

    torch = ModuleType("torch")
    torch.cuda = type(
        "FakeCuda",
        (),
        {
            "is_available": staticmethod(lambda: cuda_available),
            "get_device_name": staticmethod(lambda _: "Test GPU"),
        },
    )()
    ultralytics = ModuleType("ultralytics")
    ultralytics.YOLO = lambda _: FakeModel()
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "ultralytics", ultralytics)

    return (
        YoloEngine(
            VisionSettings(
                model_path="yolo26m.pt",
                requested_device=requested_device,
                image_size=640,
                max_image_bytes=1024 * 1024,
                max_image_pixels=1_000_000,
                max_concurrency=1,
            )
        ),
        moved_devices,
    )
