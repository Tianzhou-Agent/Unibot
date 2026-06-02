"""Validation helpers for storage facade and adapters."""

from __future__ import annotations

import re
from collections.abc import Mapping

from .errors import (
    InvalidContentTypeError,
    InvalidKeyError,
    InvalidNamespaceError,
    PayloadTooLargeError,
    StorageConfigurationError,
    StorageError,
)
from .models import StorageWrite

MAX_NAMESPACE_LENGTH = 128
MAX_KEY_LENGTH = 512
MAX_CONTENT_TYPE_LENGTH = 255
MAX_METADATA_KEY_LENGTH = 128
MAX_METADATA_VALUE_LENGTH = 1024
DEFAULT_MAX_PAYLOAD_BYTES = 16 * 1024 * 1024
DEFAULT_PAGE_SIZE = 100
DEFAULT_MAX_PAGE_SIZE = 1000

_NAMESPACE_RE = re.compile(r"^[a-z0-9_.-]+$")
_METADATA_KEY_RE = re.compile(r"^[a-z0-9_.-]+$")
_CONTENT_TYPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]*/[A-Za-z0-9][A-Za-z0-9.+_-]*$")


def validate_namespace(namespace: str) -> str:
    if not isinstance(namespace, str):
        raise InvalidNamespaceError("", "namespace must be a string")
    if not namespace:
        raise InvalidNamespaceError(namespace, "namespace is required")
    if len(namespace) > MAX_NAMESPACE_LENGTH:
        raise InvalidNamespaceError(namespace, f"namespace must be at most {MAX_NAMESPACE_LENGTH} characters")
    if _has_control_characters(namespace):
        raise InvalidNamespaceError(namespace, "namespace must not contain control characters")
    if not _NAMESPACE_RE.fullmatch(namespace):
        raise InvalidNamespaceError(
            namespace,
            "namespace may contain only lowercase letters, digits, underscores, hyphens, and dots",
        )
    return namespace


def validate_key(key: str) -> str:
    if not isinstance(key, str):
        raise InvalidKeyError("", "key must be a string")
    if not key:
        raise InvalidKeyError(key, "key is required")
    if len(key) > MAX_KEY_LENGTH:
        raise InvalidKeyError(key, f"key must be at most {MAX_KEY_LENGTH} characters")
    if "/" in key or "\\" in key:
        raise InvalidKeyError(key, "key must not contain path separators")
    if ".." in key:
        raise InvalidKeyError(key, "key must not contain path traversal segments")
    if _has_control_characters(key):
        raise InvalidKeyError(key, "key must not contain control characters")
    return key


def validate_content_type(content_type: str) -> str:
    if not isinstance(content_type, str):
        raise InvalidContentTypeError("", "content type must be a string")
    if not content_type:
        raise InvalidContentTypeError(content_type, "content type is required")
    if len(content_type) > MAX_CONTENT_TYPE_LENGTH:
        raise InvalidContentTypeError(
            content_type,
            f"content type must be at most {MAX_CONTENT_TYPE_LENGTH} characters",
        )
    try:
        content_type.encode("ascii")
    except UnicodeEncodeError as exc:
        raise InvalidContentTypeError(content_type, "content type must be ASCII") from exc
    if _has_control_characters(content_type) or any(character.isspace() for character in content_type):
        raise InvalidContentTypeError(content_type, "content type must not contain whitespace or control characters")
    if not _CONTENT_TYPE_RE.fullmatch(content_type):
        raise InvalidContentTypeError(content_type, "content type must use MIME-style type/subtype syntax")
    return content_type


def validate_metadata(metadata: Mapping[str, str] | None) -> dict[str, str]:
    if metadata is None:
        return {}
    if not isinstance(metadata, Mapping):
        raise StorageError("Storage metadata must be a mapping")

    validated: dict[str, str] = {}
    for key, value in metadata.items():
        if not isinstance(key, str):
            raise StorageError("Storage metadata keys must be strings")
        if not isinstance(value, str):
            raise StorageError("Storage metadata values must be strings")
        if not key:
            raise StorageError("Storage metadata keys are required")
        if len(key) > MAX_METADATA_KEY_LENGTH:
            raise StorageError(f"Storage metadata keys must be at most {MAX_METADATA_KEY_LENGTH} characters")
        if key.startswith("tzap-"):
            raise StorageError("Storage metadata keys must not use the reserved tzap- prefix")
        try:
            key.encode("ascii")
        except UnicodeEncodeError as exc:
            raise StorageError("Storage metadata keys must be ASCII") from exc
        if not _METADATA_KEY_RE.fullmatch(key):
            raise StorageError(
                "Storage metadata keys may contain only lowercase letters, digits, underscores, hyphens, and dots"
            )
        if _has_control_characters(value):
            raise StorageError("Storage metadata values must not contain control characters")
        if len(value) > MAX_METADATA_VALUE_LENGTH:
            raise StorageError(f"Storage metadata values must be at most {MAX_METADATA_VALUE_LENGTH} characters")
        validated[key] = value
    return validated


def validate_payload(payload: bytes, *, max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES) -> bytes:
    if not isinstance(payload, bytes):
        raise StorageError("Storage payload must be bytes")
    if max_payload_bytes < 0:
        raise StorageConfigurationError("max_payload_bytes must be non-negative")
    payload_size = len(payload)
    if payload_size > max_payload_bytes:
        raise PayloadTooLargeError(payload_size, max_payload_bytes)
    return payload


def validate_ttl(ttl: int | None) -> int | None:
    if ttl is None:
        return None
    if isinstance(ttl, bool) or not isinstance(ttl, int):
        raise StorageError("Storage TTL must be a positive integer number of seconds")
    if ttl <= 0:
        raise StorageError("Storage TTL must be a positive integer number of seconds")
    return ttl


def normalize_page_size(
    page_size: int | None,
    *,
    default_page_size: int = DEFAULT_PAGE_SIZE,
    max_page_size: int = DEFAULT_MAX_PAGE_SIZE,
) -> int:
    if isinstance(default_page_size, bool) or not isinstance(default_page_size, int) or default_page_size <= 0:
        raise StorageConfigurationError("default_page_size must be a positive integer")
    if isinstance(max_page_size, bool) or not isinstance(max_page_size, int) or max_page_size <= 0:
        raise StorageConfigurationError("max_page_size must be a positive integer")
    if default_page_size > max_page_size:
        raise StorageConfigurationError("default_page_size must not exceed max_page_size")

    if page_size is None:
        requested = default_page_size
    else:
        if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size <= 0:
            raise StorageError("page_size must be a positive integer")
        requested = page_size
    return min(requested, max_page_size)


def validate_storage_write(
    payload: bytes,
    content_type: str,
    metadata: Mapping[str, str] | None = None,
    ttl: int | None = None,
    *,
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES,
) -> StorageWrite:
    return StorageWrite(
        payload=validate_payload(payload, max_payload_bytes=max_payload_bytes),
        content_type=validate_content_type(content_type),
        metadata=validate_metadata(metadata),
        ttl=validate_ttl(ttl),
    )


def _has_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)
