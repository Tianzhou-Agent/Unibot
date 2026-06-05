from __future__ import annotations

from enum import Enum


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
