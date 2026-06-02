from __future__ import annotations

import unittest

from tianzhou_agent_platform.store.models import (
    StorageAck,
    StorageObject,
    StorageObjectSummary,
    StoragePage,
    StorageWrite,
)


class StorageModelTests(unittest.TestCase):
    def test_storage_object_preserves_payload_content_type_and_metadata(self) -> None:
        metadata = {"owner": "aina"}

        resource = StorageObject(
            payload=b"\x00hello\xff",
            content_type="application/octet-stream",
            metadata=metadata,
        )
        metadata["owner"] = "changed"

        self.assertEqual(resource.payload, b"\x00hello\xff")
        self.assertEqual(resource.content_type, "application/octet-stream")
        self.assertEqual(resource.metadata, {"owner": "aina"})

    def test_storage_ack_fields(self) -> None:
        ack = StorageAck(namespace="memory", key="thread-1", adapter="mysql")

        self.assertEqual(ack.namespace, "memory")
        self.assertEqual(ack.key, "thread-1")
        self.assertEqual(ack.adapter, "mysql")

    def test_storage_page_shape(self) -> None:
        metadata = {"kind": "note"}
        summary = StorageObjectSummary(
            key="a",
            content_type="application/json",
            metadata=metadata,
            size=42,
        )
        items = [summary]
        page = StoragePage(items=items, next_page_token="a")
        metadata["kind"] = "changed"
        items.clear()

        self.assertEqual(page.items, [summary])
        self.assertEqual(page.next_page_token, "a")
        self.assertEqual(summary.metadata, {"kind": "note"})

    def test_storage_write_contains_ttl(self) -> None:
        write = StorageWrite(
            payload=b"{}",
            content_type="application/json",
            metadata={"kind": "cache"},
            ttl=60,
        )

        self.assertEqual(write.payload, b"{}")
        self.assertEqual(write.content_type, "application/json")
        self.assertEqual(write.metadata, {"kind": "cache"})
        self.assertEqual(write.ttl, 60)


if __name__ == "__main__":
    unittest.main()
