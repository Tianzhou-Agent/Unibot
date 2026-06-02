"""Binary envelope codec for payloads stored by byte-oriented adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping

from .errors import StorageBackendError, StorageError
from .models import StorageObject
from .validation import validate_content_type, validate_metadata

ENVELOPE_MAGIC = b"TZSTOR1"
HEADER_LENGTH_BYTES = 4


def encode_envelope(payload: bytes, content_type: str, metadata: Mapping[str, str] | None = None) -> bytes:
    if not isinstance(payload, bytes):
        raise StorageError("Storage envelope payload must be bytes")
    header = {
        "content_type": validate_content_type(content_type),
        "metadata": validate_metadata(metadata),
    }
    header_bytes = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return ENVELOPE_MAGIC + len(header_bytes).to_bytes(HEADER_LENGTH_BYTES, "big") + header_bytes + payload


def decode_envelope(envelope: bytes) -> StorageObject:
    if not isinstance(envelope, bytes):
        raise StorageBackendError("Invalid storage envelope")
    header_length_start = len(ENVELOPE_MAGIC)
    header_length_end = header_length_start + HEADER_LENGTH_BYTES
    if len(envelope) < header_length_end or not envelope.startswith(ENVELOPE_MAGIC):
        raise StorageBackendError("Invalid storage envelope")

    header_length = int.from_bytes(envelope[header_length_start:header_length_end], "big")
    header_start = header_length_end
    header_end = header_start + header_length
    if header_length <= 0 or header_end > len(envelope):
        raise StorageBackendError("Invalid storage envelope")

    try:
        header = json.loads(envelope[header_start:header_end].decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StorageBackendError("Invalid storage envelope") from exc

    if not isinstance(header, dict):
        raise StorageBackendError("Invalid storage envelope")
    try:
        content_type = validate_content_type(header["content_type"])
        metadata = validate_metadata(header["metadata"])
    except (KeyError, StorageError) as exc:
        raise StorageBackendError("Invalid storage envelope") from exc

    return StorageObject(
        payload=envelope[header_end:],
        content_type=content_type,
        metadata=metadata,
    )
