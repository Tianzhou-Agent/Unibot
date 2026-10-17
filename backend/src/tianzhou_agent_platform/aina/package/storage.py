"""Plugin-private key-value storage of an AINA installation (Host API ``storage`` scope, design §8).

Each installation (tenant, user, AINA) has its own namespace; nothing in the API lets one installation name
another's keys. Values are JSON, bounded per item and per installation.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import Field

from tianzhou_agent_platform.core.base import StrictModel, utc_now
from tianzhou_agent_platform.core.errors import PlatformError

MAX_KEY_CHARS = 512
MAX_VALUE_BYTES = 256 * 1024
MAX_INSTALLATION_BYTES = 10 * 1024 * 1024
MAX_INSTALLATION_ITEMS = 10_000
MAX_LIST_ITEMS = 1_000


class AinaStorageItem(StrictModel):
    id: str = Field(pattern=r"^[0-9a-f]{64}$")
    installation_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    aina_id: str
    key: str = Field(min_length=1, max_length=MAX_KEY_CHARS)
    value: Any
    size_bytes: int = Field(ge=0)
    updated_at: datetime = Field(default_factory=utc_now)


def storage_item(*, installation_key: str, aina_id: str, key: str, value: Any) -> AinaStorageItem:
    if not key or len(key) > MAX_KEY_CHARS or any(ord(character) < 32 for character in key):
        raise PlatformError(
            "INVALID_REQUEST",
            f"Storage keys are 1-{MAX_KEY_CHARS} printable characters",
            status_code=422,
            source="aina_host",
        )
    size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    if size > MAX_VALUE_BYTES:
        raise PlatformError(
            "INVALID_REQUEST",
            f"A storage value cannot exceed {MAX_VALUE_BYTES} bytes",
            status_code=413,
            source="aina_host",
        )
    item_id = hashlib.sha256(f"{installation_key}\x1f{key}".encode()).hexdigest()
    return AinaStorageItem(
        id=item_id,
        installation_key=installation_key,
        aina_id=aina_id,
        key=key,
        value=value,
        size_bytes=size,
    )


def check_quota(*, used_bytes: int, used_items: int, replaced: AinaStorageItem | None, item: AinaStorageItem) -> None:
    """Raise when storing ``item`` (in place of ``replaced``) would exceed the installation's quota."""
    bytes_after = used_bytes - (replaced.size_bytes if replaced else 0) + item.size_bytes
    items_after = used_items + (0 if replaced else 1)
    if bytes_after > MAX_INSTALLATION_BYTES or items_after > MAX_INSTALLATION_ITEMS:
        raise PlatformError(
            "QUOTA_EXCEEDED",
            "The AINA's storage quota for this user is exhausted",
            status_code=507,
            source="aina_host",
        )
