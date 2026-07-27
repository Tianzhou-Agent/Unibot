from fastapi import APIRouter, File, Form, Request, UploadFile

from tianzhou_agent_platform.api.dependencies import settings, vision
from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.vision.models import VisionDetectionResponse, VisionHealth

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def create_vision_router() -> APIRouter:
    router = APIRouter(prefix="/vision", tags=["vision"])

    @router.get("/health", response_model=VisionHealth)
    async def vision_health(request: Request) -> VisionHealth:
        return await vision(request).health()

    @router.post("/detect", response_model=VisionDetectionResponse)
    async def detect_image(
        request: Request,
        image: UploadFile = File(...),
        confidence: float = Form(default=0.25, ge=0.01, le=1),
    ) -> VisionDetectionResponse:
        content_type = image.content_type or ""
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise PlatformError(
                "INVALID_REQUEST",
                "仅支持 JPEG、PNG 和 WebP 图片。",
                status_code=415,
                source="vision",
            )
        max_bytes = settings(request).vision_max_image_bytes
        payload = await image.read(max_bytes + 1)
        if not payload:
            raise PlatformError(
                "INVALID_REQUEST",
                "图片不能为空。",
                status_code=422,
                source="vision",
            )
        if len(payload) > max_bytes:
            raise PlatformError(
                "INVALID_REQUEST",
                "图片超过配置的大小限制。",
                status_code=413,
                source="vision",
            )
        return await vision(request).detect(
            payload=payload,
            filename=image.filename or "image",
            content_type=content_type,
            confidence=confidence,
        )

    return router
