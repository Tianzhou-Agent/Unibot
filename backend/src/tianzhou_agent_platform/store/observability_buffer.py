"""Shared record and durability contract for OBS buffering backends."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

RecordType = Literal["trace_started", "trace_finished", "span_started", "span_finished", "event"]


class ObsRecord(BaseModel):
    """One immutable observation record sent through the durable buffer."""

    schema_version: int = 1
    record_id: str = Field(default_factory=lambda: f"obsrec_{os.urandom(16).hex()}")
    record_type: RecordType
    producer_instance_id: str
    sequence_no: int
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    trace_id: str
    span_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ObsBufferError(Exception):
    """Base class for durable observation buffer failures."""


class ObsBufferFlushTimeoutError(ObsBufferError):
    """A durability barrier did not complete within its timeout."""


class ObsBufferGapError(ObsBufferError):
    """A record could not be accepted, so a durability barrier must fail."""


class DurableObsBuffer(Protocol):
    producer_instance_id: str
    metrics: Any

    def submit(self, record: ObsRecord) -> int: ...

    async def flush_through(self, sequence_no: int) -> None: ...

    def start(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


def build_producer_instance_id(
    node_id: str,
    process_id: int | None = None,
    startup_uuid: str | None = None,
) -> str:
    """Return ``<node_id>-<process_id>-<startup_uuid>``."""

    process = process_id if process_id is not None else os.getpid()
    startup = startup_uuid if startup_uuid is not None else uuid.uuid4().hex
    return f"{node_id}-{process}-{startup}"


# Compatibility aliases for the legacy file-WAL implementation and tests.
WalError = ObsBufferError
WalFlushTimeoutError = ObsBufferFlushTimeoutError
WalGapError = ObsBufferGapError
