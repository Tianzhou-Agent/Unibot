from __future__ import annotations

import asyncio
import os
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO
from time import perf_counter
from typing import Any, AsyncIterator, Protocol

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}

COCO_LABELS_ZH = {
    "person": "人",
    "bicycle": "自行车",
    "car": "汽车",
    "motorcycle": "摩托车",
    "airplane": "飞机",
    "bus": "公交车",
    "train": "火车",
    "truck": "卡车",
    "boat": "船",
    "traffic light": "交通灯",
    "fire hydrant": "消防栓",
    "stop sign": "停止标志",
    "parking meter": "停车计时器",
    "bench": "长椅",
    "bird": "鸟",
    "cat": "猫",
    "dog": "狗",
    "horse": "马",
    "sheep": "羊",
    "cow": "牛",
    "elephant": "大象",
    "bear": "熊",
    "zebra": "斑马",
    "giraffe": "长颈鹿",
    "backpack": "背包",
    "umbrella": "雨伞",
    "handbag": "手提包",
    "tie": "领带",
    "suitcase": "行李箱",
    "frisbee": "飞盘",
    "skis": "滑雪板",
    "snowboard": "单板滑雪板",
    "sports ball": "运动球",
    "kite": "风筝",
    "baseball bat": "棒球棒",
    "baseball glove": "棒球手套",
    "skateboard": "滑板",
    "surfboard": "冲浪板",
    "tennis racket": "网球拍",
    "bottle": "瓶子",
    "wine glass": "酒杯",
    "cup": "杯子",
    "fork": "叉子",
    "knife": "刀",
    "spoon": "勺子",
    "bowl": "碗",
    "banana": "香蕉",
    "apple": "苹果",
    "sandwich": "三明治",
    "orange": "橙子",
    "broccoli": "西兰花",
    "carrot": "胡萝卜",
    "hot dog": "热狗",
    "pizza": "披萨",
    "donut": "甜甜圈",
    "cake": "蛋糕",
    "chair": "椅子",
    "couch": "沙发",
    "potted plant": "盆栽",
    "bed": "床",
    "dining table": "餐桌",
    "toilet": "马桶",
    "tv": "电视",
    "laptop": "笔记本电脑",
    "mouse": "鼠标",
    "remote": "遥控器",
    "keyboard": "键盘",
    "cell phone": "手机",
    "microwave": "微波炉",
    "oven": "烤箱",
    "toaster": "烤面包机",
    "sink": "水槽",
    "refrigerator": "冰箱",
    "book": "书",
    "clock": "时钟",
    "vase": "花瓶",
    "scissors": "剪刀",
    "teddy bear": "泰迪熊",
    "hair drier": "吹风机",
    "toothbrush": "牙刷",
}


@dataclass(frozen=True)
class VisionSettings:
    model_path: str
    requested_device: str
    image_size: int
    max_image_bytes: int
    max_image_pixels: int
    max_concurrency: int

    @classmethod
    def from_env(cls) -> "VisionSettings":
        return cls(
            model_path=os.getenv("YOLO_MODEL_PATH", "yolo26m.pt"),
            requested_device=os.getenv("YOLO_DEVICE", "auto").strip().lower(),
            image_size=int(os.getenv("YOLO_IMAGE_SIZE", "640")),
            max_image_bytes=int(os.getenv("YOLO_MAX_IMAGE_BYTES", str(10 * 1024 * 1024))),
            max_image_pixels=int(os.getenv("YOLO_MAX_IMAGE_PIXELS", "40000000")),
            max_concurrency=int(os.getenv("YOLO_MAX_CONCURRENCY", "1")),
        )


class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class Detection(BaseModel):
    class_id: int
    label: str
    label_zh: str
    confidence: float = Field(ge=0, le=1)
    box: BoundingBox


class ImageMetadata(BaseModel):
    width: int
    height: int


class DetectionResponse(BaseModel):
    model: str
    device: str
    image: ImageMetadata
    detections: list[Detection]
    summary: dict[str, int]
    inference_ms: float


class HealthResponse(BaseModel):
    status: str
    model: str
    device: str
    requested_device: str
    gpu_name: str | None = None


class DetectionEngine(Protocol):
    async def load(self) -> None: ...

    def health(self) -> HealthResponse: ...

    async def detect(self, image: Image.Image, confidence: float) -> DetectionResponse: ...


class YoloEngine:
    def __init__(self, settings: VisionSettings) -> None:
        self.settings = settings
        self.model: Any | None = None
        self.device = "cpu"
        self.gpu_name: str | None = None
        self._semaphore = asyncio.Semaphore(max(1, settings.max_concurrency))

    async def load(self) -> None:
        await run_in_threadpool(self._load_sync)

    def _load_sync(self) -> None:
        import torch
        from ultralytics import YOLO

        requested = self.settings.requested_device
        if requested == "auto":
            self.device = "cuda:0" if torch.cuda.is_available() else "cpu"
        elif requested.startswith("cuda"):
            self.device = requested if torch.cuda.is_available() else "cpu"
        elif requested == "cpu":
            self.device = "cpu"
        else:
            raise RuntimeError("YOLO_DEVICE must be auto, cpu, cuda, or cuda:<index>")

        if self.device.startswith("cuda"):
            index = int(self.device.partition(":")[2] or "0")
            self.gpu_name = torch.cuda.get_device_name(index)

        self.model = YOLO(self.settings.model_path)
        self.model.to(self.device)

    def health(self) -> HealthResponse:
        return HealthResponse(
            status="ready" if self.model is not None else "loading",
            model=os.path.basename(self.settings.model_path),
            device=self.device,
            requested_device=self.settings.requested_device,
            gpu_name=self.gpu_name,
        )

    async def detect(self, image: Image.Image, confidence: float) -> DetectionResponse:
        if self.model is None:
            raise RuntimeError("YOLO model is not loaded")
        async with self._semaphore:
            return await run_in_threadpool(self._detect_sync, image, confidence)

    def _detect_sync(self, image: Image.Image, confidence: float) -> DetectionResponse:
        started = perf_counter()
        results = self.model.predict(
            source=image,
            conf=confidence,
            imgsz=self.settings.image_size,
            device=self.device,
            verbose=False,
        )
        result = results[0]
        detections: list[Detection] = []
        if result.boxes is not None:
            coordinates = result.boxes.xyxy.detach().cpu().tolist()
            confidences = result.boxes.conf.detach().cpu().tolist()
            class_ids = result.boxes.cls.detach().cpu().tolist()
            names = result.names
            for coordinates_row, score, class_value in zip(coordinates, confidences, class_ids, strict=True):
                class_id = int(class_value)
                label = str(names[class_id])
                detections.append(
                    Detection(
                        class_id=class_id,
                        label=label,
                        label_zh=COCO_LABELS_ZH.get(label, label),
                        confidence=round(float(score), 6),
                        box=BoundingBox(
                            x1=round(float(coordinates_row[0]), 2),
                            y1=round(float(coordinates_row[1]), 2),
                            x2=round(float(coordinates_row[2]), 2),
                            y2=round(float(coordinates_row[3]), 2),
                        ),
                    )
                )
        detections.sort(key=lambda item: item.confidence, reverse=True)
        summary = dict(Counter(item.label_zh for item in detections))
        return DetectionResponse(
            model=os.path.basename(self.settings.model_path),
            device=self.device,
            image=ImageMetadata(width=image.width, height=image.height),
            detections=detections,
            summary=summary,
            inference_ms=round((perf_counter() - started) * 1000, 2),
        )


def create_app(
    *,
    settings: VisionSettings | None = None,
    engine: DetectionEngine | None = None,
) -> FastAPI:
    resolved_settings = settings or VisionSettings.from_env()
    resolved_engine = engine or YoloEngine(resolved_settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await resolved_engine.load()
        yield

    app = FastAPI(title="Unibot YOLO Vision Service", version="1.0.0", lifespan=lifespan)
    app.state.engine = resolved_engine
    app.state.settings = resolved_settings

    @app.get("/healthz", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return resolved_engine.health()

    @app.post("/v1/detect", response_model=DetectionResponse)
    async def detect(
        image: UploadFile = File(...),
        confidence: float = Form(default=0.25, ge=0.01, le=1),
    ) -> DetectionResponse:
        if image.content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=415, detail="Only JPEG, PNG, and WebP images are supported")
        payload = await image.read(resolved_settings.max_image_bytes + 1)
        if not payload:
            raise HTTPException(status_code=422, detail="Image is empty")
        if len(payload) > resolved_settings.max_image_bytes:
            raise HTTPException(status_code=413, detail="Image exceeds the configured size limit")
        try:
            decoded = _decode_image(payload, resolved_settings.max_image_pixels)
        except (UnidentifiedImageError, OSError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="Image data is invalid or unsafe") from exc
        try:
            return await resolved_engine.detect(decoded, confidence)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    return app


def _decode_image(payload: bytes, max_pixels: int) -> Image.Image:
    with Image.open(BytesIO(payload)) as source:
        source.verify()
    with Image.open(BytesIO(payload)) as source:
        width, height = source.size
        if width <= 0 or height <= 0 or width * height > max_pixels:
            raise ValueError("Image dimensions exceed the configured pixel limit")
        return source.convert("RGB")


app = create_app()
