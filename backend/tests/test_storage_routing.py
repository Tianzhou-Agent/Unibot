from __future__ import annotations

import unittest

from tianzhou_agent_platform.store.errors import (
    InvalidNamespaceError,
    StorageConfigurationError,
    UnknownNamespaceError,
)
from tianzhou_agent_platform.store.routing import StorageRouteResolver


class StorageRoutingTests(unittest.TestCase):
    def test_explicit_namespace_route_wins(self) -> None:
        resolver = StorageRouteResolver(
            adapter_names=["mysql", "redis"],
            routes={"cache": "redis"},
            default_adapter="mysql",
        )

        resolution = resolver.resolve("cache")

        self.assertEqual(resolution.namespace, "cache")
        self.assertEqual(resolution.adapter_name, "redis")

    def test_default_adapter_fallback(self) -> None:
        resolver = StorageRouteResolver(adapter_names=["mysql"], default_adapter="mysql")

        self.assertEqual(resolver.resolve("memory").adapter_name, "mysql")

    def test_unknown_namespace_without_default(self) -> None:
        resolver = StorageRouteResolver(adapter_names=["mysql"], routes={"memory": "mysql"})

        with self.assertRaises(UnknownNamespaceError):
            resolver.resolve("cache")

    def test_route_referencing_missing_adapter_fails_configuration(self) -> None:
        with self.assertRaises(StorageConfigurationError):
            StorageRouteResolver(adapter_names=["mysql"], routes={"cache": "redis"})

    def test_missing_default_adapter_fails_configuration(self) -> None:
        with self.assertRaises(StorageConfigurationError):
            StorageRouteResolver(adapter_names=["mysql"], default_adapter="redis")

    def test_resolve_rejects_invalid_namespace(self) -> None:
        resolver = StorageRouteResolver(adapter_names=["mysql"], default_adapter="mysql")

        with self.assertRaises(InvalidNamespaceError):
            resolver.resolve("Bad Namespace")


if __name__ == "__main__":
    unittest.main()
