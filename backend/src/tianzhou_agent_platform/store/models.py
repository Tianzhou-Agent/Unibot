"""Shared storage data models."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class StorageObject:
    payload: bytes
    content_type: str
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class StorageObjectSummary:
    key: str
    content_type: str
    metadata: dict[str, str] = field(default_factory=dict)
    size: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class StoragePage:
    items: list[StorageObjectSummary]
    next_page_token: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", list(self.items))


@dataclass(frozen=True)
class StorageAck:
    namespace: str
    key: str
    adapter: str


@dataclass(frozen=True)
class StorageWrite:
    payload: bytes
    content_type: str
    metadata: dict[str, str] = field(default_factory=dict)
    ttl: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata))
