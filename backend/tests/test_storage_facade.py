from __future__ import annotations

import asyncio
import unittest

from tianzhou_agent_platform import store
from tianzhou_agent_platform.store.errors import (
    AdapterUnavailableError,
    AlreadyExistsError,
    InvalidKeyError,
    InvalidNamespaceError,
    NotFoundError,
    PayloadTooLargeError,
    StorageBackendError,
    StorageConfigurationError,
    StorageError,
    StorageTimeoutError,
    UnsupportedOperationError,
)
from tianzhou_agent_platform.store.facade import StorageFacade
from tianzhou_agent_platform.store.lifecycle import StorageLifecycleManager
from tianzhou_agent_platform.store.models import StorageAck, StorageObject, StorageObjectSummary, StoragePage, StorageWrite


class InMemoryAdapter:
    supports_ttl = False
    supports_ordered_list = True

    def __init__(self, name: str = "memory") -> None:
        self.name = name
        self.resources: dict[tuple[str, str], StorageObject] = {}
        self.list_calls: list[tuple[str, str | None, int, str | None]] = []

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def get(self, namespace: str, key: str) -> StorageObject | None:
        return self.resources.get((namespace, key))

    async def exists(self, namespace: str, key: str) -> bool:
        return (namespace, key) in self.resources

    async def create(self, namespace: str, key: str, resource: StorageWrite) -> StorageAck:
        storage_key = (namespace, key)
        if storage_key in self.resources:
            raise AlreadyExistsError(namespace, key)
        self.resources[storage_key] = StorageObject(
            payload=resource.payload,
            content_type=resource.content_type,
            metadata=resource.metadata,
        )
        return StorageAck(namespace=namespace, key=key, adapter=self.name)

    async def put(self, namespace: str, key: str, resource: StorageWrite) -> StorageAck:
        self.resources[(namespace, key)] = StorageObject(
            payload=resource.payload,
            content_type=resource.content_type,
            metadata=resource.metadata,
        )
        return StorageAck(namespace=namespace, key=key, adapter=self.name)

    async def delete(self, namespace: str, key: str) -> bool:
        return self.resources.pop((namespace, key), None) is not None

    async def list(
        self,
        namespace: str,
        prefix: str | None,
        page_size: int,
        page_token: str | None,
    ) -> StoragePage:
        self.list_calls.append((namespace, prefix, page_size, page_token))
        keys = sorted(
            key
            for resource_namespace, key in self.resources
            if resource_namespace == namespace
            and (prefix is None or key.startswith(prefix))
            and (page_token is None or key > page_token)
        )
        page_keys = keys[:page_size]
        items = [
            StorageObjectSummary(
                key=key,
                content_type=self.resources[(namespace, key)].content_type,
                metadata=self.resources[(namespace, key)].metadata,
                size=len(self.resources[(namespace, key)].payload),
            )
            for key in page_keys
        ]
        next_page_token = page_keys[-1] if len(keys) > page_size else None
        return StoragePage(items=items, next_page_token=next_page_token)


class NoListAdapter(InMemoryAdapter):
    supports_ordered_list = False


class RawFailingAdapter(InMemoryAdapter):
    async def get(self, namespace: str, key: str) -> StorageObject | None:
        raise RuntimeError("password=secret raw backend failure")


class SlowAdapter(InMemoryAdapter):
    async def get(self, namespace: str, key: str) -> StorageObject | None:
        await asyncio.sleep(0.05)
        return StorageObject(payload=b"slow", content_type="text/plain", metadata={})


class RecordingMetricsHook:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def record(
        self,
        *,
        operation: str,
        adapter: str,
        namespace: str,
        duration_seconds: float,
        success: bool,
        error_category: str | None,
    ) -> None:
        self.records.append(
            {
                "operation": operation,
                "adapter": adapter,
                "namespace": namespace,
                "duration_seconds": duration_seconds,
                "success": success,
                "error_category": error_category,
            }
        )


class BrokenMetricsHook:
    def record(
        self,
        *,
        operation: str,
        adapter: str,
        namespace: str,
        duration_seconds: float,
        success: bool,
        error_category: str | None,
    ) -> None:
        raise RuntimeError("broken metrics hook")


class StorageFacadeTests(unittest.IsolatedAsyncioTestCase):
    async def _facade_for(
        self,
        adapter: InMemoryAdapter,
        *,
        default_timeout_seconds: float = 5.0,
        metrics_hook: RecordingMetricsHook | None = None,
        default_page_size: int = 2,
        max_page_size: int = 10,
        max_payload_bytes: int = 1024,
    ) -> StorageFacade:
        manager = StorageLifecycleManager(
            adapters={adapter.name: adapter},
            routes={"memory": adapter.name},
            default_adapter=adapter.name,
        )
        await manager.startup()
        self.addAsyncCleanup(manager.shutdown)
        return StorageFacade(
            manager,
            default_timeout_seconds=default_timeout_seconds,
            metrics_hook=metrics_hook,
            default_page_size=default_page_size,
            max_page_size=max_page_size,
            max_payload_bytes=max_payload_bytes,
        )

    async def test_crud_and_ordered_list(self) -> None:
        adapter = InMemoryAdapter()
        facade = await self._facade_for(adapter)

        create_ack = await facade.create(
            "memory",
            "b",
            b"first",
            "text/plain",
            metadata={"kind": "note"},
        )
        await facade.put("memory", "a", b"second", "text/plain", metadata={"kind": "note"})
        await facade.put("memory", "c", b"third", "text/plain", metadata={"kind": "note"})

        self.assertEqual(create_ack.adapter, "memory")
        self.assertTrue(await facade.exists("memory", "b"))
        resource = await facade.get("memory", "b")
        self.assertIsNotNone(resource)
        self.assertEqual(resource.payload, b"first")
        self.assertEqual(resource.metadata, {"kind": "note"})

        await facade.put("memory", "b", b"replacement", "application/octet-stream", metadata={"kind": "binary"})
        replaced = await facade.get("memory", "b")
        self.assertIsNotNone(replaced)
        self.assertEqual(replaced.payload, b"replacement")
        self.assertEqual(replaced.content_type, "application/octet-stream")
        self.assertEqual(replaced.metadata, {"kind": "binary"})

        first_page = await facade.list("memory")
        self.assertEqual([item.key for item in first_page.items], ["a", "b"])
        self.assertEqual(first_page.next_page_token, "b")
        second_page = await facade.list("memory", page_token=first_page.next_page_token)
        self.assertEqual([item.key for item in second_page.items], ["c"])
        self.assertIsNone(second_page.next_page_token)

        delete_ack = await facade.delete("memory", "b")
        self.assertEqual(delete_ack.key, "b")
        self.assertFalse(await facade.exists("memory", "b"))

    async def test_duplicate_create_preserves_already_exists_error(self) -> None:
        facade = await self._facade_for(InMemoryAdapter())

        await facade.create("memory", "item", b"one", "text/plain")

        with self.assertRaises(AlreadyExistsError):
            await facade.create("memory", "item", b"two", "text/plain")

    async def test_missing_ok_behavior(self) -> None:
        facade = await self._facade_for(InMemoryAdapter())

        with self.assertRaises(NotFoundError):
            await facade.get("memory", "missing")
        self.assertIsNone(await facade.get("memory", "missing", missing_ok=True))

        with self.assertRaises(NotFoundError):
            await facade.delete("memory", "missing")
        ack = await facade.delete("memory", "missing", missing_ok=True)
        self.assertEqual(ack.namespace, "memory")
        self.assertEqual(ack.key, "missing")
        self.assertEqual(ack.adapter, "memory")

    async def test_timeout_is_normalized(self) -> None:
        facade = await self._facade_for(SlowAdapter(), default_timeout_seconds=0.001)

        with self.assertRaises(StorageTimeoutError) as caught:
            await facade.get("memory", "slow")

        self.assertEqual(caught.exception.operation, "get")
        self.assertEqual(caught.exception.namespace, "memory")
        self.assertEqual(caught.exception.adapter, "memory")
        self.assertTrue(caught.exception.retryable)

    async def test_raw_backend_error_is_sanitized(self) -> None:
        facade = await self._facade_for(RawFailingAdapter())

        with self.assertRaises(StorageBackendError) as caught:
            await facade.get("memory", "item")

        self.assertEqual(caught.exception.operation, "get")
        self.assertEqual(caught.exception.adapter, "memory")
        self.assertIsNone(caught.exception.__cause__)
        self.assertIsNone(caught.exception.__context__)
        self.assertNotIn("password=secret", str(caught.exception))
        self.assertNotIn("raw backend failure", str(caught.exception))

    async def test_unsupported_ttl_and_list_are_rejected_before_adapter_operation(self) -> None:
        ttl_adapter = InMemoryAdapter()
        ttl_facade = await self._facade_for(ttl_adapter)

        with self.assertRaises(UnsupportedOperationError):
            await ttl_facade.create("memory", "cache", b"payload", "text/plain", ttl=30)
        self.assertFalse(await ttl_facade.exists("memory", "cache"))

        with self.assertRaises(UnsupportedOperationError):
            await ttl_facade.put("memory", "put-cache", b"payload", "text/plain", ttl=30)
        self.assertFalse(await ttl_facade.exists("memory", "put-cache"))

        no_list_facade = await self._facade_for(NoListAdapter())
        with self.assertRaises(UnsupportedOperationError):
            await no_list_facade.list("memory")

    async def test_unavailable_adapter_is_rejected(self) -> None:
        adapter = InMemoryAdapter()
        manager = StorageLifecycleManager(
            adapters={adapter.name: adapter},
            routes={"memory": adapter.name},
            default_adapter=adapter.name,
        )
        facade = StorageFacade(manager)

        with self.assertRaises(AdapterUnavailableError):
            await facade.exists("memory", "item")

    async def test_invalid_facade_configuration_fails(self) -> None:
        adapter = InMemoryAdapter()
        manager = StorageLifecycleManager(
            adapters={adapter.name: adapter},
            routes={"memory": adapter.name},
            default_adapter=adapter.name,
        )

        with self.assertRaises(StorageConfigurationError):
            StorageFacade(manager, max_payload_bytes=-1)

    async def test_facade_validation_errors_are_preserved(self) -> None:
        facade = await self._facade_for(InMemoryAdapter(), max_payload_bytes=3)

        with self.assertRaises(InvalidNamespaceError):
            await facade.exists("Bad Namespace", "item")

        with self.assertRaises(InvalidKeyError):
            await facade.exists("memory", "folder/item")

        with self.assertRaises(PayloadTooLargeError):
            await facade.put("memory", "too-big", b"large", "text/plain")

    async def test_page_size_is_normalized_before_adapter_list(self) -> None:
        adapter = InMemoryAdapter()
        facade = await self._facade_for(adapter, default_page_size=2, max_page_size=3)
        for key in ["a", "b", "c", "d"]:
            await facade.put("memory", key, key.encode("ascii"), "text/plain")

        page = await facade.list("memory", page_size=10)

        self.assertEqual([item.key for item in page.items], ["a", "b", "c"])
        self.assertEqual(adapter.list_calls[-1], ("memory", None, 3, None))

        with self.assertRaises(StorageError):
            await facade.list("memory", page_size=0)

    async def test_logs_exclude_payload_contents(self) -> None:
        facade = await self._facade_for(InMemoryAdapter())

        with self.assertLogs("tianzhou_agent_platform.store.facade", level="INFO") as logs:
            await facade.create("memory", "log-item", b"top-secret-payload", "text/plain")

        rendered_logs = "\n".join(logs.output)
        self.assertIn("operation=create", rendered_logs)
        self.assertIn("adapter=memory", rendered_logs)
        self.assertIn("namespace=memory", rendered_logs)
        self.assertNotIn("started", rendered_logs)
        self.assertNotIn("top-secret-payload", rendered_logs)

    async def test_metrics_hook_records_success_and_failure(self) -> None:
        metrics = RecordingMetricsHook()
        facade = await self._facade_for(InMemoryAdapter(), metrics_hook=metrics)

        await facade.create("memory", "item", b"payload", "text/plain")
        with self.assertRaises(NotFoundError):
            await facade.get("memory", "missing")

        self.assertEqual(len(metrics.records), 2)
        self.assertEqual(metrics.records[0]["operation"], "create")
        self.assertTrue(metrics.records[0]["success"])
        self.assertIsNone(metrics.records[0]["error_category"])
        self.assertEqual(metrics.records[1]["operation"], "get")
        self.assertFalse(metrics.records[1]["success"])
        self.assertEqual(metrics.records[1]["error_category"], "NotFoundError")

    async def test_metrics_hook_records_timeout_and_backend_error_categories(self) -> None:
        timeout_metrics = RecordingMetricsHook()
        timeout_facade = await self._facade_for(
            SlowAdapter(),
            default_timeout_seconds=0.001,
            metrics_hook=timeout_metrics,
        )

        with self.assertRaises(StorageTimeoutError):
            await timeout_facade.get("memory", "slow")

        backend_metrics = RecordingMetricsHook()
        backend_facade = await self._facade_for(RawFailingAdapter(), metrics_hook=backend_metrics)

        with self.assertRaises(StorageBackendError):
            await backend_facade.get("memory", "raw")

        self.assertEqual(timeout_metrics.records[0]["error_category"], "StorageTimeoutError")
        self.assertEqual(backend_metrics.records[0]["error_category"], "StorageBackendError")

    async def test_metrics_hook_failure_does_not_fail_operation(self) -> None:
        facade = await self._facade_for(InMemoryAdapter(), metrics_hook=BrokenMetricsHook())  # type: ignore[arg-type]

        ack = await facade.create("memory", "item", b"payload", "text/plain")

        self.assertEqual(ack.key, "item")

    async def test_module_level_wrappers_delegate_to_configured_facade(self) -> None:
        facade = await self._facade_for(InMemoryAdapter())
        store.configure_storage(facade)
        self.addCleanup(store.configure_storage, None)

        await store.create("memory", "item", b"payload", "text/plain")
        resource = await store.get("memory", "item")

        self.assertIsNotNone(resource)
        self.assertEqual(resource.payload, b"payload")

    async def test_module_level_wrappers_require_configuration(self) -> None:
        store.configure_storage(None)

        with self.assertRaises(StorageConfigurationError):
            await store.exists("memory", "item")


if __name__ == "__main__":
    unittest.main()
