from __future__ import annotations

from typing import Any

import httpx
from pydantic import ValidationError

from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.vision.models import VisionDetectionResponse, VisionHealth

_INVALID_IMAGE_MESSAGES = {
    413: "图片超过配置的大小限制。",
    415: "仅支持 JPEG、PNG 和 WebP 图片。",
    422: "图片数据无效或不安全。",
}


class VisionClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._owns_client = http_client is None
        self._client = http_client or httpx.AsyncClient(timeout=timeout_seconds)
        self._timeout = timeout_seconds

    async def health(self) -> VisionHealth:
        response = await self._request("GET", "/healthz")
        return self._validate(VisionHealth, response)

    async def detect(
        self,
        *,
        payload: bytes,
        filename: str,
        content_type: str,
        confidence: float,
    ) -> VisionDetectionResponse:
        response = await self._request(
            "POST",
            "/v1/detect",
            files={"image": (filename, payload, content_type)},
            data={"confidence": str(confidence)},
        )
        return self._validate(VisionDetectionResponse, response)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = await self._client.request(
                method,
                f"{self.base_url}{path}",
                timeout=self._timeout,
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise PlatformError(
                "TIMEOUT",
                "YOLO vision service timed out",
                status_code=504,
                retryable=True,
                source="vision",
                user_message="图片识别超时，请稍后重试。",
            ) from exc
        except httpx.HTTPError as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "YOLO vision service is unavailable",
                status_code=503,
                retryable=True,
                source="vision",
                user_message="图片识别服务暂时不可用。",
            ) from exc
        if response.is_success:
            return response

        detail = _response_detail(response)
        if response.status_code in {413, 415, 422}:
            raise PlatformError(
                "INVALID_REQUEST",
                detail,
                status_code=response.status_code,
                source="vision",
                user_message=_INVALID_IMAGE_MESSAGES[response.status_code],
            )
        raise PlatformError(
            "DEPENDENCY_FAILED",
            f"YOLO vision service failed with HTTP {response.status_code}: {detail}",
            status_code=503,
            retryable=response.status_code >= 500,
            source="vision",
            user_message="图片识别服务执行失败，请稍后重试。",
        )

    @staticmethod
    def _validate(model: type[VisionHealth] | type[VisionDetectionResponse], response: httpx.Response):
        try:
            return model.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise PlatformError(
                "DEPENDENCY_FAILED",
                "YOLO vision service returned an invalid response",
                status_code=502,
                source="vision",
                user_message="图片识别服务返回了无效结果。",
            ) from exc


def _response_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or "Vision service request failed"
    if isinstance(body, dict):
        detail = body.get("detail")
        if isinstance(detail, str) and detail:
            return detail
    return "Vision service request failed"
