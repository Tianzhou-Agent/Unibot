from __future__ import annotations

from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from tianzhou_agent_platform.core.errors import PlatformError
from tianzhou_agent_platform.store.errors import StorageError, StorageErrorCode


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(PlatformError)
    async def platform_error_handler(request: Request, exc: PlatformError) -> JSONResponse:
        return _error_response(
            request,
            status_code=exc.status_code,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
            source=exc.source,
            user_message=exc.user_message or exc.message,
        )

    @app.exception_handler(StorageError)
    async def storage_error_handler(request: Request, exc: StorageError) -> JSONResponse:
        status_codes = {
            StorageErrorCode.NOT_FOUND: 404,
            StorageErrorCode.VALIDATION_FAILURE: 422,
            StorageErrorCode.POLICY_VIOLATION: 403,
            StorageErrorCode.TIMEOUT: 504,
            StorageErrorCode.BACKEND_UNAVAILABLE: 503,
            StorageErrorCode.UNSUPPORTED_CAPABILITY: 501,
            StorageErrorCode.UNKNOWN_BACKEND_FAILURE: 500,
        }
        codes = {
            StorageErrorCode.NOT_FOUND: "RESOURCE_NOT_FOUND",
            StorageErrorCode.VALIDATION_FAILURE: "INVALID_REQUEST",
            StorageErrorCode.POLICY_VIOLATION: "PERMISSION_DENIED",
            StorageErrorCode.TIMEOUT: "TIMEOUT",
            StorageErrorCode.BACKEND_UNAVAILABLE: "DEPENDENCY_FAILED",
            StorageErrorCode.UNSUPPORTED_CAPABILITY: "DEPENDENCY_FAILED",
            StorageErrorCode.UNKNOWN_BACKEND_FAILURE: "INTERNAL_ERROR",
        }
        return _error_response(
            request,
            status_code=status_codes[exc.code],
            code=codes[exc.code],
            message=exc.message,
            retryable=exc.code in {StorageErrorCode.TIMEOUT, StorageErrorCode.BACKEND_UNAVAILABLE},
            source="storage",
            user_message="The storage service is temporarily unavailable.",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error_response(
            request,
            status_code=422,
            code="INVALID_REQUEST",
            message="The request did not match the API schema",
            retryable=False,
            source="api",
            user_message="Please check the request fields and try again.",
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "RESOURCE_NOT_FOUND" if exc.status_code == 404 else "INVALID_REQUEST"
        return _error_response(
            request,
            status_code=exc.status_code,
            code=code,
            message=str(exc.detail),
            retryable=False,
            source="api",
            user_message=str(exc.detail),
        )


def _error_response(
    request: Request,
    *,
    status_code: int,
    code: str,
    message: str,
    retryable: bool,
    source: str,
    user_message: str,
) -> JSONResponse:
    trace_id = getattr(request.state, "request_trace_id", f"request_{uuid4().hex}")
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "source": source,
                "user_message": user_message,
                "trace_id": trace_id,
            }
        },
        headers={"X-Trace-ID": trace_id},
    )
