from __future__ import annotations

from datetime import datetime, timezone

from tianzhou_agent_platform.store.observability_store import _event_values
from tianzhou_agent_platform.store.observability_buffer import ObsRecord


def test_event_values_keep_microsecond_and_wal_order() -> None:
    occurred_at = datetime(2026, 8, 10, 7, 0, 0, 123456, tzinfo=timezone.utc)
    values = _event_values(
        ObsRecord(
            record_type="event",
            producer_instance_id="node-1",
            sequence_no=42,
            occurred_at=occurred_at,
            trace_id="trace-1",
            payload={"name": "model.completed", "occurred_at": occurred_at.isoformat()},
        )
    )

    assert values["record_version"] == 1_786_345_200_123_456
    assert values["sequence_no"] == 42
