from __future__ import annotations

import unittest

from tianzhou_agent_platform.store.errors import (
    AdapterUnavailableError,
    StorageBackendError,
    StorageConfigurationError,
)
from tianzhou_agent_platform.store.lifecycle import StorageLifecycleManager
from tianzhou_agent_platform.store.models import StorageAck, StorageObject, StoragePage, StorageWrite


class FakeAdapter:
    supports_ttl = False
    supports_ordered_list = True

    def __init__(
        self,
        name: str,
        events: list[str],
        *,
        startup_error: Exception | None = None,
        shutdown_error: Exception | None = None,
    ) -> None:
        self.name = name
        self._events = events
        self._startup_error = startup_error
        self._shutdown_error = shutdown_error

    async def startup(self) -> None:
        self._events.append(f"{self.name}.startup")
        if self._startup_error is not None:
            raise self._startup_error

    async def shutdown(self) -> None:
        self._events.append(f"{self.name}.shutdown")
        if self._shutdown_error is not None:
            raise self._shutdown_error

    async def get(self, namespace: str, key: str) -> StorageObject | None:
        return None

    async def exists(self, namespace: str, key: str) -> bool:
        return False

    async def create(self, namespace: str, key: str, resource: StorageWrite) -> StorageAck:
        return StorageAck(namespace=namespace, key=key, adapter=self.name)

    async def put(self, namespace: str, key: str, resource: StorageWrite) -> StorageAck:
        return StorageAck(namespace=namespace, key=key, adapter=self.name)

    async def delete(self, namespace: str, key: str) -> bool:
        return False

    async def list(
        self,
        namespace: str,
        prefix: str | None,
        page_size: int,
        page_token: str | None,
    ) -> StoragePage:
        return StoragePage(items=[])


class StorageLifecycleManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_initializes_adapters_and_allows_resolution(self) -> None:
        events: list[str] = []
        mysql = FakeAdapter("mysql", events)
        redis = FakeAdapter("redis", events)
        manager = StorageLifecycleManager(
            adapters={"mysql": mysql, "redis": redis},
            routes={"cache": "redis"},
            default_adapter="mysql",
        )

        await manager.startup()

        self.assertEqual(events, ["mysql.startup", "redis.startup"])
        self.assertTrue(manager.is_available("mysql"))
        self.assertTrue(manager.is_available("redis"))
        self.assertIs(manager.get_adapter("mysql"), mysql)
        self.assertIs(manager.resolve_adapter("cache").adapter, redis)
        self.assertIs(manager.resolve_adapter("memory").adapter, mysql)

    async def test_shutdown_closes_started_adapters_in_reverse_order(self) -> None:
        events: list[str] = []
        manager = StorageLifecycleManager(
            adapters={
                "mysql": FakeAdapter("mysql", events),
                "redis": FakeAdapter("redis", events),
            },
            default_adapter="mysql",
        )

        await manager.startup()
        await manager.shutdown()

        self.assertEqual(
            events,
            ["mysql.startup", "redis.startup", "redis.shutdown", "mysql.shutdown"],
        )
        self.assertFalse(manager.is_available("mysql"))
        with self.assertRaises(AdapterUnavailableError):
            manager.get_adapter("mysql")

    async def test_shutdown_before_startup_is_noop(self) -> None:
        events: list[str] = []
        manager = StorageLifecycleManager(
            adapters={"mysql": FakeAdapter("mysql", events)},
            default_adapter="mysql",
        )

        await manager.shutdown()

        self.assertEqual(events, [])

    async def test_startup_failure_cleans_up_started_adapters_and_marks_unavailable(self) -> None:
        events: list[str] = []
        manager = StorageLifecycleManager(
            adapters={
                "mysql": FakeAdapter("mysql", events),
                "redis": FakeAdapter("redis", events, startup_error=RuntimeError("redis://secret")),
            },
            default_adapter="mysql",
        )

        with self.assertRaises(AdapterUnavailableError):
            await manager.startup()

        self.assertEqual(events, ["mysql.startup", "redis.startup", "mysql.shutdown"])
        self.assertFalse(manager.is_available("mysql"))
        self.assertFalse(manager.is_available("redis"))
        with self.assertRaises(AdapterUnavailableError):
            manager.get_adapter("redis")

    async def test_storage_error_from_startup_is_preserved(self) -> None:
        events: list[str] = []
        manager = StorageLifecycleManager(
            adapters={
                "mysql": FakeAdapter("mysql", events),
                "redis": FakeAdapter(
                    "redis",
                    events,
                    startup_error=StorageConfigurationError("bad redis configuration"),
                ),
            },
            default_adapter="mysql",
        )

        with self.assertRaises(StorageConfigurationError):
            await manager.startup()

        self.assertEqual(events, ["mysql.startup", "redis.startup", "mysql.shutdown"])

    async def test_mark_unavailable_rejects_adapter_access(self) -> None:
        events: list[str] = []
        manager = StorageLifecycleManager(
            adapters={"mysql": FakeAdapter("mysql", events)},
            default_adapter="mysql",
        )

        await manager.startup()
        manager.mark_unavailable("mysql")

        with self.assertRaises(AdapterUnavailableError):
            manager.get_adapter("mysql")

        manager.mark_available("mysql")

        self.assertTrue(manager.is_available("mysql"))

    async def test_shutdown_failure_is_normalized_after_all_shutdowns_are_attempted(self) -> None:
        events: list[str] = []
        manager = StorageLifecycleManager(
            adapters={
                "mysql": FakeAdapter("mysql", events),
                "redis": FakeAdapter("redis", events, shutdown_error=RuntimeError("raw backend secret")),
            },
            default_adapter="mysql",
        )

        await manager.startup()

        with self.assertRaises(StorageBackendError) as caught:
            await manager.shutdown()

        self.assertEqual(
            events,
            ["mysql.startup", "redis.startup", "redis.shutdown", "mysql.shutdown"],
        )
        self.assertEqual(caught.exception.adapter, "redis")
        self.assertNotIn("raw backend secret", str(caught.exception))

    def test_route_referencing_missing_adapter_fails_configuration(self) -> None:
        events: list[str] = []

        with self.assertRaises(StorageConfigurationError):
            StorageLifecycleManager(
                adapters={"mysql": FakeAdapter("mysql", events)},
                routes={"cache": "redis"},
            )

    def test_adapter_name_mismatch_fails_configuration(self) -> None:
        events: list[str] = []

        with self.assertRaises(StorageConfigurationError):
            StorageLifecycleManager(
                adapters={"mysql": FakeAdapter("redis", events)},
                default_adapter="mysql",
            )

    def test_unknown_adapter_access_fails_configuration(self) -> None:
        events: list[str] = []
        manager = StorageLifecycleManager(
            adapters={"mysql": FakeAdapter("mysql", events)},
            default_adapter="mysql",
        )

        with self.assertRaises(StorageConfigurationError):
            manager.get_adapter("redis")


if __name__ == "__main__":
    unittest.main()
