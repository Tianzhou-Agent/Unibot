"""Image Recognition MCP server: object detection with the YOLO vision service.

The canvas sends the user's image to ``detect``; the image only exists for the inference and is never stored. Tool
errors start with a code (``UNSUPPORTED_TYPE: ...``) that the canvas shows in the user's language.
"""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any

import httpx
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}

mcp = MCPServer("image-recognition", version="2.0.0")
# Tests route vision-service calls to a fake; None means real HTTP.
VISION_TRANSPORT: httpx.AsyncBaseTransport | None = None


class VisionSettings(BaseSettings):
    # Read from the environment or, bundled with Unibot, the backend's .env like the host's own settings.
    model_config = SettingsConfigDict(
        env_prefix="UNIBOT_VISION_", env_file=Path(__file__).resolve().parents[3] / ".env", extra="ignore"
    )

    base_url: str = "http://127.0.0.1:18081"
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_image_bytes: int = Field(default=10 * 1024 * 1024, ge=1024, le=50 * 1024 * 1024)


class BoundingBox(BaseModel):
    x1: float
    y1: float
    x2: float
    y2: float


class Detection(BaseModel):
    class_id: int = Field(ge=0)
    label: str
    label_zh: str
    confidence: float = Field(ge=0, le=1)
    box: BoundingBox


class ImageSize(BaseModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class DetectionResult(BaseModel):
    model: str
    device: str
    image: ImageSize
    detections: list[Detection]
    summary: dict[str, int]
    inference_ms: float = Field(ge=0)


class VisionStatus(BaseModel):
    status: str
    model: str
    device: str
    requested_device: str
    gpu_name: str | None = None


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
async def vision_status() -> VisionStatus:
    """Whether the vision service is ready, with its model and device."""
    return _parse(VisionStatus, await _request("GET", "/healthz"))


@mcp.tool(annotations=ToolAnnotations(read_only_hint=True))
async def detect(
    image_base64: str, content_type: str, filename: str = "image", confidence: float = 0.25
) -> DetectionResult:
    """Detect the objects in an image (JPEG, PNG or WebP): labels, confidence and boxes in image pixels."""
    if content_type not in ALLOWED_TYPES:
        raise ToolError("UNSUPPORTED_TYPE: only JPEG, PNG and WebP images are supported")
    if not 0.01 <= confidence <= 1:
        raise ToolError("INVALID_IMAGE: confidence must be between 0.01 and 1")
    try:
        payload = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ToolError("INVALID_IMAGE: the image is not valid base64") from exc
    if not payload:
        raise ToolError("INVALID_IMAGE: the image is empty")
    if len(payload) > VisionSettings().max_image_bytes:
        raise ToolError("TOO_LARGE: the image exceeds the configured size limit")
    response = await _request(
        "POST",
        "/v1/detect",
        files={"image": (filename[:200] or "image", payload, content_type)},
        data={"confidence": str(confidence)},
    )
    return _parse(DetectionResult, response)


async def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    settings = VisionSettings()
    try:
        async with httpx.AsyncClient(transport=VISION_TRANSPORT, timeout=settings.timeout_seconds) as client:
            response = await client.request(method, f"{settings.base_url.rstrip('/')}{path}", **kwargs)
    except httpx.TimeoutException as exc:
        raise ToolError("TIMEOUT: the vision service timed out") from exc
    except httpx.HTTPError as exc:
        raise ToolError("UNAVAILABLE: the vision service is unavailable") from exc
    if response.is_success:
        return response
    if response.status_code == 413:
        raise ToolError("TOO_LARGE: the image exceeds the configured size limit")
    if response.status_code == 415:
        raise ToolError("UNSUPPORTED_TYPE: only JPEG, PNG and WebP images are supported")
    if response.status_code == 422:
        raise ToolError("INVALID_IMAGE: the image data is invalid or unsafe")
    raise ToolError(f"FAILED: the vision service failed with HTTP {response.status_code}")


def _parse[Model: BaseModel](model: type[Model], response: httpx.Response) -> Model:
    try:
        return model.model_validate(response.json())
    except (ValueError, ValidationError) as exc:
        raise ToolError("FAILED: the vision service returned an invalid result") from exc


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8767, stateless_http=True)
