"""Public storage facade."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol, TypeVar

from .errors import (
    NotFoundError,
    StorageBackendError,
    StorageConfigurationError,
    StorageError,
    StorageTimeoutError,
    UnsupportedOperationError,
)
from .lifecycle import StorageLifecycleManager
from .models import StorageAck, StorageObject, StoragePage, StorageWrite
from .validation import (
    DEFAULT_MAX_PAGE_SIZE,
    DEFAULT_MAX_PAYLOAD_BYTES,
    DEFAULT_PAGE_SIZE,
    normalize_page_size,
    validate_key,
    validate_namespace,
    validate_storage_write,
)

_T = TypeVar("_T")
_logger = logging.getLogger(__name__)
_default_facade: StorageFacade | None = None


class StorageMetricsHook(Protocol):
    def record(
        self,
        *,
        operation: str,
        adapter: str,
        namespace: str,
        duration_seconds: float,
        success: bool,
        error_category: str | None,
    ) -> None: ...


class StorageFacade:
    def __init__(
        self,
        lifecycle_manager: StorageLifecycleManager,
        *,
        default_timeout_seconds: float = 5.0,
        max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
        default_page_size: int = DEFAULT_PAGE_SIZE,
        max_page_size: int = DEFAULT_MAX_PAGE_SIZE,
        metrics_hook: StorageMetricsHook | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        if default_timeout_seconds <= 0:
            raise StorageConfigurationError("default_timeout_seconds must be positive")
        if max_payload_bytes < 0:
            raise StorageConfigurationError("max_payload_bytes must be non-negative")
        normalize_page_size(
            None,
            default_page_size=default_page_size,
            max_page_size=max_page_size,
        )
        self._lifecycle_manager = lifecycle_manager
        self._default_timeout_seconds = default_timeout_seconds
        self._max_payload_bytes = max_payload_bytes
        self._default_page_size = default_page_size
        self._max_page_size = max_page_size
        self._metrics_hook = metrics_hook
        self._logger = logger or _logger

    async def get(self, namespace: str, key: str, *, missing_ok: bool = False) -> StorageObject | None:
        namespace = validate_namespace(namespace)
        key = validate_key(key)
        adapter_name = self._resolve_adapter_name(namespace)

        async def operation() -> StorageObject | None:
            adapter = self._lifecycle_manager.get_adapter(adapter_name)
            result = await adapter.get(namespace, key)
            if result is None and not missing_ok:
                raise NotFoundError(namespace, key)
            return result

        return await self._run("get", namespace, adapter_name, operation)

    async def exists(self, namespace: str, key: str) -> bool:
        namespace = validate_namespace(namespace)
        key = validate_key(key)
        adapter_name = self._resolve_adapter_name(namespace)

        async def operation() -> bool:
            adapter = self._lifecycle_manager.get_adapter(adapter_name)
            return await adapter.exists(namespace, key)

        return await self._run("exists", namespace, adapter_name, operation)

    async def create(
        self,
        namespace: str,
        key: str,
        payload: bytes,
        content_type: str,
        *,
        metadata: Mapping[str, str] | None = None,
        ttl: int | None = None,
    ) -> StorageAck:
        resource = self._validate_write(namespace, key, payload, content_type, metadata, ttl)
        adapter_name = self._resolve_adapter_name(namespace)

        async def operation() -> StorageAck:
            adapter = self._lifecycle_manager.get_adapter(adapter_name)
            if resource.ttl is not None and not adapter.supports_ttl:
                raise UnsupportedOperationError("create", adapter_name, "adapter does not support TTL")
            return await adapter.create(namespace, key, resource)

        return await self._run("create", namespace, adapter_name, operation)

    async def put(
        self,
        namespace: str,
        key: str,
        payload: bytes,
        content_type: str,
        *,
        metadata: Mapping[str, str] | None = None,
        ttl: int | None = None,
    ) -> StorageAck:
        resource = self._validate_write(namespace, key, payload, content_type, metadata, ttl)
        adapter_name = self._resolve_adapter_name(namespace)

        async def operation() -> StorageAck:
            adapter = self._lifecycle_manager.get_adapter(adapter_name)
            if resource.ttl is not None and not adapter.supports_ttl:
                raise UnsupportedOperationError("put", adapter_name, "adapter does not support TTL")
            return await adapter.put(namespace, key, resource)

        return await self._run("put", namespace, adapter_name, operation)

    async def delete(self, namespace: str, key: str, *, missing_ok: bool = False) -> StorageAck:
        namespace = validate_namespace(namespace)
        key = validate_key(key)
        adapter_name = self._resolve_adapter_name(namespace)

        async def operation() -> StorageAck:
            adapter = self._lifecycle_manager.get_adapter(adapter_name)
            deleted = await adapter.delete(namespace, key)
            if not deleted and not missing_ok:
                raise NotFoundError(namespace, key)
            return StorageAck(namespace=namespace, key=key, adapter=adapter_name)

        return await self._run("delete", namespace, adapter_name, operation)

    async def list(
        self,
        namespace: str,
        *,
        prefix: str | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
    ) -> StoragePage:
        namespace = validate_namespace(namespace)
        if prefix:
            validate_key(prefix)
        if page_token:
            validate_key(page_token)
        normalized_page_size = normalize_page_size(
            page_size,
            default_page_size=self._default_page_size,
            max_page_size=self._max_page_size,
        )
        adapter_name = self._resolve_adapter_name(namespace)

        async def operation() -> StoragePage:
            adapter = self._lifecycle_manager.get_adapter(adapter_name)
            if not adapter.supports_ordered_list:
                raise UnsupportedOperationError("list", adapter_name, "adapter does not support ordered listing")
            return await adapter.list(namespace, prefix, normalized_page_size, page_token)

        return await self._run("list", namespace, adapter_name, operation)

    def _validate_write(
        self,
        namespace: str,
        key: str,
        payload: bytes,
        content_type: str,
        metadata: Mapping[str, str] | None,
        ttl: int | None,
    ) -> StorageWrite:
        validate_namespace(namespace)
        validate_key(key)
        return validate_storage_write(
            payload,
            content_type,
            metadata,
            ttl,
            max_payload_bytes=self._max_payload_bytes,
        )

    def _resolve_adapter_name(self, namespace: str) -> str:
        return self._lifecycle_manager.route_resolver.resolve(namespace).adapter_name

    async def _run(
        self,
        operation: str,
        namespace: str,
        adapter_name: str,
        callback: Callable[[], Awaitable[_T]],
    ) -> _T:
        self._logger.debug(
            "storage operation started operation=%s adapter=%s namespace=%s",
            operation,
            adapter_name,
            namespace,
            extra={
                "operation": operation,
                "adapter": adapter_name,
                "namespace": namespace,
                "event": "storage_operation_started",
            },
        )
        started_at = time.monotonic()
        backend_error: StorageBackendError | None = None
        try:
            result = await asyncio.wait_for(callback(), timeout=self._default_timeout_seconds)
        except asyncio.TimeoutError as exc:
            duration = time.monotonic() - started_at
            error = StorageTimeoutError(operation, namespace, adapter_name, retryable=True)
            self._record_completion(operation, namespace, adapter_name, duration, False, type(error).__name__)
            raise error from exc
        except StorageError as exc:
            duration = time.monotonic() - started_at
            self._record_completion(operation, namespace, adapter_name, duration, False, type(exc).__name__)
            raise
        except Exception:
            duration = time.monotonic() - started_at
            error = StorageBackendError(
                operation=operation,
                adapter=adapter_name,
                retryable=False,
            )
            self._record_completion(operation, namespace, adapter_name, duration, False, type(error).__name__)
            backend_error = error

        if backend_error is not None:
            raise backend_error

        duration = time.monotonic() - started_at
        self._record_completion(operation, namespace, adapter_name, duration, True, None)
        return result

    def _record_completion(
        self,
        operation: str,
        namespace: str,
        adapter_name: str,
        duration_seconds: float,
        success: bool,
        error_category: str | None,
    ) -> None:
        self._logger.info(
            "storage operation completed operation=%s adapter=%s namespace=%s success=%s duration_seconds=%.6f error_category=%s",
            operation,
            adapter_name,
            namespace,
            success,
            duration_seconds,
            error_category,
            extra={
                "operation": operation,
                "adapter": adapter_name,
                "namespace": namespace,
                "event": "storage_operation_completed",
                "success": success,
                "duration_seconds": duration_seconds,
                "error_category": error_category,
            },
        )
        if self._metrics_hook is None:
            return
        try:
            self._metrics_hook.record(
                operation=operation,
                adapter=adapter_name,
                namespace=namespace,
                duration_seconds=duration_seconds,
                success=success,
                error_category=error_category,
            )
        except Exception:
            self._logger.debug("storage metrics hook failed", exc_info=True)


def configure_storage(facade: StorageFacade | None) -> None:
    global _default_facade
    _default_facade = facade


def get_storage_facade() -> StorageFacade:
    if _default_facade is None:
        raise StorageConfigurationError("Storage facade is not configured")
    return _default_facade


async def get(namespace: str, key: str, *, missing_ok: bool = False) -> StorageObject | None:
    return await get_storage_facade().get(namespace, key, missing_ok=missing_ok)


async def exists(namespace: str, key: str) -> bool:
    return await get_storage_facade().exists(namespace, key)


async def create(
    namespace: str,
    key: str,
    payload: bytes,
    content_type: str,
    *,
    metadata: Mapping[str, str] | None = None,
    ttl: int | None = None,
) -> StorageAck:
    return await get_storage_facade().create(
        namespace,
        key,
        payload,
        content_type,
        metadata=metadata,
        ttl=ttl,
    )


async def put(
    namespace: str,
    key: str,
    payload: bytes,
    content_type: str,
    *,
    metadata: Mapping[str, str] | None = None,
    ttl: int | None = None,
) -> StorageAck:
    return await get_storage_facade().put(
        namespace,
        key,
        payload,
        content_type,
        metadata=metadata,
        ttl=ttl,
    )


async def delete(namespace: str, key: str, *, missing_ok: bool = False) -> StorageAck:
    return await get_storage_facade().delete(namespace, key, missing_ok=missing_ok)


async def list(
    namespace: str,
    *,
    prefix: str | None = None,
    page_size: int | None = None,
    page_token: str | None = None,
) -> StoragePage:
    return await get_storage_facade().list(
        namespace,
        prefix=prefix,
        page_size=page_size,
        page_token=page_token,
    )
