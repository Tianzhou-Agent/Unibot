from pydantic import Field

from tianzhou_agent_platform.core.base import StrictModel


class VisionBoundingBox(StrictModel):
    x1: float
    y1: float
    x2: float
    y2: float


class VisionDetection(StrictModel):
    class_id: int = Field(ge=0)
    label: str
    label_zh: str
    confidence: float = Field(ge=0, le=1)
    box: VisionBoundingBox


class VisionImageMetadata(StrictModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class VisionDetectionResponse(StrictModel):
    model: str
    device: str
    image: VisionImageMetadata
    detections: list[VisionDetection]
    summary: dict[str, int]
    inference_ms: float = Field(ge=0)


class VisionHealth(StrictModel):
    status: str
    model: str
    device: str
    requested_device: str
    gpu_name: str | None = None
