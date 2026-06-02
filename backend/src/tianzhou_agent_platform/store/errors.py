"""Storage-layer error hierarchy."""

from __future__ import annotations


class StorageError(Exception):
    """Base class for all storage-layer errors."""


class StorageConfigurationError(StorageError):
    """Raised for invalid storage configuration."""


class UnknownNamespaceError(StorageError):
    def __init__(self, namespace: str) -> None:
        self.namespace = namespace
        super().__init__(f"Unknown storage namespace: {namespace!r}")


class InvalidNamespaceError(StorageError):
    def __init__(self, namespace: str, reason: str) -> None:
        self.namespace = namespace
        self.reason = reason
        super().__init__(f"Invalid storage namespace: {reason}")


class InvalidKeyError(StorageError):
    def __init__(self, key: str, reason: str) -> None:
        self.key = key
        self.reason = reason
        super().__init__(f"Invalid storage key: {reason}")


class InvalidContentTypeError(StorageError):
    def __init__(self, content_type: str, reason: str) -> None:
        self.content_type = content_type
        self.reason = reason
        super().__init__(f"Invalid content type: {reason}")


class NotFoundError(StorageError):
    def __init__(self, namespace: str, key: str) -> None:
        self.namespace = namespace
        self.key = key
        super().__init__(f"Storage resource not found: {namespace!r}/{key!r}")


class AlreadyExistsError(StorageError):
    def __init__(self, namespace: str, key: str) -> None:
        self.namespace = namespace
        self.key = key
        super().__init__(f"Storage resource already exists: {namespace!r}/{key!r}")


class UnsupportedOperationError(StorageError):
    def __init__(self, operation: str, adapter: str | None = None, reason: str | None = None) -> None:
        self.operation = operation
        self.adapter = adapter
        self.reason = reason
        adapter_text = f" on adapter {adapter!r}" if adapter else ""
        reason_text = f": {reason}" if reason else ""
        super().__init__(f"Unsupported storage operation {operation!r}{adapter_text}{reason_text}")


class AdapterUnavailableError(StorageError):
    def __init__(self, adapter: str) -> None:
        self.adapter = adapter
        super().__init__(f"Storage adapter unavailable: {adapter!r}")


class StorageTimeoutError(StorageError):
    def __init__(self, operation: str, namespace: str, adapter: str, *, retryable: bool = True) -> None:
        self.operation = operation
        self.namespace = namespace
        self.adapter = adapter
        self.retryable = retryable
        super().__init__(
            f"Storage operation {operation!r} timed out for namespace {namespace!r} "
            f"on adapter {adapter!r}"
        )


class PayloadTooLargeError(StorageError):
    def __init__(self, payload_size: int, max_payload_bytes: int) -> None:
        self.payload_size = payload_size
        self.max_payload_bytes = max_payload_bytes
        super().__init__(
            f"Storage payload is too large: {payload_size} bytes exceeds {max_payload_bytes} bytes"
        )


class StorageBackendError(StorageError):
    def __init__(
        self,
        message: str = "Storage backend operation failed",
        *,
        operation: str | None = None,
        adapter: str | None = None,
        retryable: bool = False,
    ) -> None:
        self.operation = operation
        self.adapter = adapter
        self.retryable = retryable
        super().__init__(message)
