from __future__ import annotations

import unittest

from tianzhou_agent_platform.store.codec import ENVELOPE_MAGIC, decode_envelope, encode_envelope
from tianzhou_agent_platform.store.errors import StorageBackendError, StorageError


class StorageCodecTests(unittest.TestCase):
    def test_envelope_round_trip_preserves_binary_payload_content_type_and_metadata(self) -> None:
        payload = b"\x00hello\xff"
        envelope = encode_envelope(
            payload=payload,
            content_type="application/octet-stream",
            metadata={"kind": "binary"},
        )

        decoded = decode_envelope(envelope)

        self.assertTrue(envelope.startswith(ENVELOPE_MAGIC))
        self.assertEqual(decoded.payload, payload)
        self.assertEqual(decoded.content_type, "application/octet-stream")
        self.assertEqual(decoded.metadata, {"kind": "binary"})

    def test_envelope_encoding_is_deterministic(self) -> None:
        first = encode_envelope(b"payload", "application/json", {"b": "2", "a": "1"})
        second = encode_envelope(b"payload", "application/json", {"a": "1", "b": "2"})

        self.assertEqual(first, second)

    def test_corrupt_envelopes_are_rejected(self) -> None:
        invalid = [
            b"",
            b"BADSTOR" + b"\x00\x00\x00\x02{}",
            ENVELOPE_MAGIC + b"\x00\x00\x00\x00",
            ENVELOPE_MAGIC + b"\x00\x00\x00\x10{}",
            ENVELOPE_MAGIC + b"\x00\x00\x00\x02{}",
        ]

        for envelope in invalid:
            with self.subTest(envelope=envelope):
                with self.assertRaises(StorageBackendError):
                    decode_envelope(envelope)

    def test_non_bytes_inputs_are_rejected(self) -> None:
        with self.assertRaises(StorageError):
            encode_envelope(bytearray(b"payload"), "application/json")  # type: ignore[arg-type]

        with self.assertRaises(StorageBackendError):
            decode_envelope("not bytes")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
