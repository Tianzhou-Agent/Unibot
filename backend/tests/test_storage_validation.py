from __future__ import annotations

import unittest

from tianzhou_agent_platform.store.errors import (
    InvalidContentTypeError,
    InvalidKeyError,
    InvalidNamespaceError,
    PayloadTooLargeError,
    StorageConfigurationError,
    StorageError,
)
from tianzhou_agent_platform.store.validation import (
    MAX_KEY_LENGTH,
    MAX_METADATA_KEY_LENGTH,
    MAX_METADATA_VALUE_LENGTH,
    MAX_NAMESPACE_LENGTH,
    normalize_page_size,
    validate_content_type,
    validate_key,
    validate_metadata,
    validate_namespace,
    validate_payload,
    validate_storage_write,
    validate_ttl,
)


class StorageValidationTests(unittest.TestCase):
    def test_valid_namespaces(self) -> None:
        for namespace in ["memory", "cache.v1", "local_files", "attachments-1"]:
            with self.subTest(namespace=namespace):
                self.assertEqual(validate_namespace(namespace), namespace)

    def test_invalid_namespaces(self) -> None:
        invalid = ["", "Memory", "memory/files", "bad namespace", "bad\nname", "a" * (MAX_NAMESPACE_LENGTH + 1)]

        for namespace in invalid:
            with self.subTest(namespace=namespace):
                with self.assertRaises(InvalidNamespaceError):
                    validate_namespace(namespace)

    def test_key_rejects_empty_traversal_separators_control_chars_and_length(self) -> None:
        invalid = ["", "../x", "a..b", "folder/key", "folder\\key", "bad\x1fname", "a" * (MAX_KEY_LENGTH + 1)]

        for key in invalid:
            with self.subTest(key=key):
                with self.assertRaises(InvalidKeyError):
                    validate_key(key)

    def test_key_allows_opaque_non_path_text(self) -> None:
        self.assertEqual(validate_key("thread id 42"), "thread id 42")

    def test_content_type_validation(self) -> None:
        for content_type in ["application/json", "image/svg+xml", "application/vnd.test+json"]:
            with self.subTest(content_type=content_type):
                self.assertEqual(validate_content_type(content_type), content_type)

        for content_type in ["", "json", "application /json", "application/json\n", "application/json utf-8"]:
            with self.subTest(content_type=content_type):
                with self.assertRaises(InvalidContentTypeError):
                    validate_content_type(content_type)

    def test_metadata_validation(self) -> None:
        metadata = {"kind": "note", "agent.id": "unibot", "file_name": "hello"}

        self.assertEqual(validate_metadata(metadata), metadata)

    def test_metadata_rejects_violations(self) -> None:
        invalid_metadata = [
            {"Kind": "note"},
            {"tzap-owner": "system"},
            {"bad/key": "value"},
            {"": "value"},
            {"a" * (MAX_METADATA_KEY_LENGTH + 1): "value"},
            {"kind": "bad\x00value"},
            {"kind": "a" * (MAX_METADATA_VALUE_LENGTH + 1)},
            {1: "value"},
            {"kind": 1},
        ]

        for metadata in invalid_metadata:
            with self.subTest(metadata=metadata):
                with self.assertRaises(StorageError):
                    validate_metadata(metadata)  # type: ignore[arg-type]

    def test_payload_limit(self) -> None:
        self.assertEqual(validate_payload(b"ok", max_payload_bytes=2), b"ok")

        with self.assertRaises(PayloadTooLargeError):
            validate_payload(b"too large", max_payload_bytes=3)

        with self.assertRaises(StorageError):
            validate_payload(bytearray(b"ok"))  # type: ignore[arg-type]

        with self.assertRaises(StorageConfigurationError):
            validate_payload(b"ok", max_payload_bytes=-1)

    def test_ttl_validation(self) -> None:
        self.assertIsNone(validate_ttl(None))
        self.assertEqual(validate_ttl(1), 1)

        for ttl in [0, -1, 1.2, True]:
            with self.subTest(ttl=ttl):
                with self.assertRaises(StorageError):
                    validate_ttl(ttl)  # type: ignore[arg-type]

    def test_page_size_normalization(self) -> None:
        self.assertEqual(normalize_page_size(None, default_page_size=25, max_page_size=100), 25)
        self.assertEqual(normalize_page_size(200, default_page_size=25, max_page_size=100), 100)

        for page_size in [0, -1, 1.5, False]:
            with self.subTest(page_size=page_size):
                with self.assertRaises(StorageError):
                    normalize_page_size(page_size)  # type: ignore[arg-type]

        with self.assertRaises(StorageConfigurationError):
            normalize_page_size(None, default_page_size=0)

        with self.assertRaises(StorageConfigurationError):
            normalize_page_size(None, max_page_size=0)

        with self.assertRaises(StorageConfigurationError):
            normalize_page_size(None, default_page_size=101, max_page_size=100)

    def test_validate_storage_write(self) -> None:
        write = validate_storage_write(
            payload=b"{}",
            content_type="application/json",
            metadata={"kind": "json"},
            ttl=10,
            max_payload_bytes=2,
        )

        self.assertEqual(write.payload, b"{}")
        self.assertEqual(write.content_type, "application/json")
        self.assertEqual(write.metadata, {"kind": "json"})
        self.assertEqual(write.ttl, 10)


if __name__ == "__main__":
    unittest.main()
