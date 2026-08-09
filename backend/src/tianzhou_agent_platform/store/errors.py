from __future__ import annotations

from enum import Enum

from tianzhou_agent_platform.core.errors import PlatformError


class StorageErrorCode(str, Enum):
    NOT_FOUND = "not_found"
    VALIDATION_FAILURE = "validation_failure"
    POLICY_VIOLATION = "policy_violation"
    TIMEOUT = "timeout"
    BACKEND_UNAVAILABLE = "backend_unavailable"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    UNKNOWN_BACKEND_FAILURE = "unknown_backend_failure"


class StorageError(Exception):
    def __init__(self, code: StorageErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class StorageNotFoundError(StorageError):
    def __init__(self, message: str) -> None:
        super().__init__(StorageErrorCode.NOT_FOUND, message)


class StorageValidationError(StorageError):
    def __init__(self, message: str) -> None:
        super().__init__(StorageErrorCode.VALIDATION_FAILURE, message)


class StoragePolicyViolationError(StorageError):
    def __init__(self, message: str) -> None:
        super().__init__(StorageErrorCode.POLICY_VIOLATION, message)


class StorageTimeoutError(StorageError):
    def __init__(self, message: str) -> None:
        super().__init__(StorageErrorCode.TIMEOUT, message)


class StorageBackendUnavailableError(StorageError):
    def __init__(self, message: str) -> None:
        super().__init__(StorageErrorCode.BACKEND_UNAVAILABLE, message)


class StorageUnsupportedCapabilityError(StorageError):
    def __init__(self, message: str) -> None:
        super().__init__(StorageErrorCode.UNSUPPORTED_CAPABILITY, message)


class StorageUnknownBackendError(StorageError):
    def __init__(self, message: str) -> None:
        super().__init__(StorageErrorCode.UNKNOWN_BACKEND_FAILURE, message)


#: Single source of truth mapping StorageErrorCode to PlatformError semantics.
STORAGE_ERROR_MAP: dict[StorageErrorCode, tuple[str, int, bool]] = {
    StorageErrorCode.NOT_FOUND: ("RESOURCE_NOT_FOUND", 404, False),
    StorageErrorCode.VALIDATION_FAILURE: ("INVALID_REQUEST", 422, False),
    StorageErrorCode.POLICY_VIOLATION: ("PERMISSION_DENIED", 403, False),
    StorageErrorCode.TIMEOUT: ("TIMEOUT", 504, True),
    StorageErrorCode.BACKEND_UNAVAILABLE: ("DEPENDENCY_FAILED", 503, True),
    StorageErrorCode.UNSUPPORTED_CAPABILITY: ("DEPENDENCY_FAILED", 501, False),
    StorageErrorCode.UNKNOWN_BACKEND_FAILURE: ("INTERNAL_ERROR", 500, False),
}


def storage_error_to_platform(error: StorageError, *, message: str | None = None) -> PlatformError:
    code, status_code, retryable = STORAGE_ERROR_MAP[error.code]
    return PlatformError(
        code,
        message or error.message,
        status_code=status_code,
        retryable=retryable,
        source="storage",
    )
