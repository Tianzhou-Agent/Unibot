"""In-memory stand-in for the durable OBS buffer (``DurableObsBuffer``) used by the Redis pipeline in production."""

from __future__ import annotations

from typing import Any

from tianzhou_agent_platform.store.observability_buffer import ObsRecord


class _Metrics:
    def snapshot(self) -> dict[str, Any]:
        return {}


class InMemoryObsBuffer:
    """A record is durable as soon as it is submitted.

    Durable records reach ``store`` in sequence order when a barrier covers them or the buffer closes, the way the
    Redis ingest worker hands them to OBS MySQL.
    """

    def __init__(self, producer_instance_id: str, store: Any | None = None) -> None:
        self.producer_instance_id = producer_instance_id
        self.metrics = _Metrics()
        self.records: list[ObsRecord] = []
        self.flushed_through = 0
        self._store = store
        self._delivered = 0

    @property
    def sequence_no(self) -> int:
        return len(self.records)

    def submit(self, record: ObsRecord) -> int:
        record.sequence_no = len(self.records) + 1
        self.records.append(record)
        return record.sequence_no

    async def flush_through(self, sequence_no: int) -> None:
        self.flushed_through = max(self.flushed_through, min(sequence_no, self.sequence_no))
        await self._deliver(self.flushed_through)

    def start(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        await self._deliver(self.sequence_no)

    async def _deliver(self, through: int) -> None:
        if self._store is None or through <= self._delivered:
            return
        batch = self.records[self._delivered : through]
        self._delivered = through
        await self._store.bulk_upsert(batch)
