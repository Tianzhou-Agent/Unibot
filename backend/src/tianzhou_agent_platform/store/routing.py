"""Namespace route resolution for storage adapters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .errors import StorageConfigurationError, UnknownNamespaceError
from .validation import validate_namespace


@dataclass(frozen=True)
class RouteResolution:
    namespace: str
    adapter_name: str


class StorageRouteResolver:
    def __init__(
        self,
        adapter_names: Iterable[str],
        routes: Mapping[str, str] | None = None,
        default_adapter: str | None = None,
    ) -> None:
        self._adapter_names = frozenset(adapter_names)
        self._routes = dict(routes or {})
        self._default_adapter = default_adapter
        self._validate_configuration()

    def resolve(self, namespace: str) -> RouteResolution:
        namespace = validate_namespace(namespace)
        adapter_name = self._routes.get(namespace)
        if adapter_name is not None:
            return RouteResolution(namespace=namespace, adapter_name=adapter_name)
        if self._default_adapter is not None:
            return RouteResolution(namespace=namespace, adapter_name=self._default_adapter)
        raise UnknownNamespaceError(namespace)

    @property
    def routes(self) -> dict[str, str]:
        return dict(self._routes)

    @property
    def default_adapter(self) -> str | None:
        return self._default_adapter

    def _validate_configuration(self) -> None:
        if not self._adapter_names:
            raise StorageConfigurationError("At least one storage adapter must be configured")
        for namespace, adapter_name in self._routes.items():
            validate_namespace(namespace)
            if adapter_name not in self._adapter_names:
                raise StorageConfigurationError(
                    f"Storage route for namespace {namespace!r} references unconfigured adapter {adapter_name!r}"
                )
        if self._default_adapter is not None and self._default_adapter not in self._adapter_names:
            raise StorageConfigurationError(
                f"Default storage adapter {self._default_adapter!r} is not configured"
            )
