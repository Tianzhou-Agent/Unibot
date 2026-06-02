from __future__ import annotations

import unittest

from tianzhou_agent_platform.store import errors


class StorageErrorTests(unittest.TestCase):
    def test_concrete_errors_are_catchable_as_storage_error(self) -> None:
        samples = [
            errors.StorageConfigurationError("bad configuration"),
            errors.UnknownNamespaceError("missing"),
            errors.InvalidNamespaceError("Bad", "invalid"),
            errors.InvalidKeyError("../x", "invalid"),
            errors.InvalidContentTypeError("json", "invalid"),
            errors.NotFoundError("memory", "item"),
            errors.AlreadyExistsError("memory", "item"),
            errors.UnsupportedOperationError("list", "redis"),
            errors.AdapterUnavailableError("mysql"),
            errors.StorageTimeoutError("get", "memory", "mysql"),
            errors.PayloadTooLargeError(2, 1),
            errors.StorageBackendError(),
        ]

        for sample in samples:
            with self.subTest(error=type(sample).__name__):
                try:
                    raise sample
                except errors.StorageError as caught:
                    self.assertIs(caught, sample)

    def test_timeout_error_exposes_operation_context_and_retryability(self) -> None:
        error = errors.StorageTimeoutError("get", "memory", "mysql", retryable=False)

        self.assertEqual(error.operation, "get")
        self.assertEqual(error.namespace, "memory")
        self.assertEqual(error.adapter, "mysql")
        self.assertFalse(error.retryable)

    def test_backend_error_exposes_operation_context_and_retryability(self) -> None:
        error = errors.StorageBackendError(
            operation="put",
            adapter="s3",
            retryable=True,
        )

        self.assertEqual(error.operation, "put")
        self.assertEqual(error.adapter, "s3")
        self.assertTrue(error.retryable)

    def test_payload_too_large_error_exposes_payload_sizes(self) -> None:
        error = errors.PayloadTooLargeError(2, 1)

        self.assertEqual(error.payload_size, 2)
        self.assertEqual(error.max_payload_bytes, 1)


if __name__ == "__main__":
    unittest.main()
