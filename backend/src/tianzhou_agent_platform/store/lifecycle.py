"""Adapter lifecycle management."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from .adapters import StorageAdapter
from .errors import AdapterUnavailableError, StorageBackendError, StorageConfigurationError, StorageError
from .routing import StorageRouteResolver


@dataclass(frozen=True)
class ManagedAdapter:
    namespace: str
    adapter_name: str
    adapter: StorageAdapter


class StorageLifecycleManager:
    def __init__(
        self,
        adapters: Mapping[str, StorageAdapter],
        *,
        routes: Mapping[str, str] | None = None,
        default_adapter: str | None = None,
    ) -> None:
        self._adapters = dict(adapters)
        self._validate_adapter_configuration()
        self._route_resolver = StorageRouteResolver(
            adapter_names=self._adapters.keys(),
            routes=routes,
            default_adapter=default_adapter,
        )
        self._started_adapters: list[str] = []
        self._unavailable_adapters: set[str] = set(self._adapters)
        self._running = False

    @property
    def route_resolver(self) -> StorageRouteResolver:
        return self._route_resolver

    @property
    def adapter_names(self) -> tuple[str, ...]:
        return tuple(self._adapters)

    def is_available(self, adapter_name: str) -> bool:
        return self._running and adapter_name in self._adapters and adapter_name not in self._unavailable_adapters

    async def startup(self) -> None:
        if self._running:
            return

        self._started_adapters = []
        self._unavailable_adapters = set(self._adapters)
        try:
            for adapter_name, adapter in self._adapters.items():
                await adapter.startup()
                self._started_adapters.append(adapter_name)
                self._unavailable_adapters.discard(adapter_name)
        except StorageError:
            await self._shutdown_started_adapters()
            self._running = False
            raise
        except Exception as exc:
            await self._shutdown_started_adapters()
            self._running = False
            raise AdapterUnavailableError(adapter_name) from exc

        self._running = True

    async def shutdown(self) -> None:
        first_error: StorageBackendError | None = None
        for adapter_name in reversed(self._started_adapters):
            try:
                await self._adapters[adapter_name].shutdown()
            except Exception as exc:
                if first_error is None:
                    first_error = StorageBackendError(
                        "Storage adapter shutdown failed",
                        adapter=adapter_name,
                        retryable=False,
                    )
                    first_error.__cause__ = exc

        self._started_adapters = []
        self._unavailable_adapters = set(self._adapters)
        self._running = False

        if first_error is not None:
            raise first_error

    def mark_unavailable(self, adapter_name: str) -> None:
        self._require_configured_adapter(adapter_name)
        self._unavailable_adapters.add(adapter_name)

    def mark_available(self, adapter_name: str) -> None:
        self._require_configured_adapter(adapter_name)
        if not self._running:
            raise AdapterUnavailableError(adapter_name)
        self._unavailable_adapters.discard(adapter_name)

    def get_adapter(self, adapter_name: str) -> StorageAdapter:
        self._require_configured_adapter(adapter_name)
        if not self.is_available(adapter_name):
            raise AdapterUnavailableError(adapter_name)
        return self._adapters[adapter_name]

    def resolve_adapter(self, namespace: str) -> ManagedAdapter:
        route = self._route_resolver.resolve(namespace)
        return ManagedAdapter(
            namespace=route.namespace,
            adapter_name=route.adapter_name,
            adapter=self.get_adapter(route.adapter_name),
        )

    async def _shutdown_started_adapters(self) -> None:
        try:
            await self.shutdown()
        except StorageBackendError:
            pass

    def _require_configured_adapter(self, adapter_name: str) -> None:
        if adapter_name not in self._adapters:
            raise StorageConfigurationError(f"Storage adapter {adapter_name!r} is not configured")

    def _validate_adapter_configuration(self) -> None:
        if not self._adapters:
            raise StorageConfigurationError("At least one storage adapter must be configured")
        for adapter_name, adapter in self._adapters.items():
            if adapter.name != adapter_name:
                raise StorageConfigurationError(
                    f"Storage adapter mapping key {adapter_name!r} does not match adapter name {adapter.name!r}"
                )
