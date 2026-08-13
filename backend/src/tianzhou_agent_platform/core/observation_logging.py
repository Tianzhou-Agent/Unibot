"""Standard-library logging interceptor backed by the durable observation WAL."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from tianzhou_agent_platform.core.observation_context import (
    current_observation_context,
    is_observation_suppressed,
    suppress_observation,
)
from tianzhou_agent_platform.core.trace_details import summarize_trace_data
from tianzhou_agent_platform.store.observability_wal import ObsRecord, WalWriter


class ObservationLogHandler(logging.Handler):
    """Capture contextual WARNING+ logs as replay-idempotent event records."""

    def __init__(self, wal_writer: WalWriter, *, level: int = logging.WARNING) -> None:
        super().__init__(level=level)
        self._wal = wal_writer
        self.dropped_count = 0

    def emit(self, record: logging.LogRecord) -> None:
        context = current_observation_context()
        if context is None or is_observation_suppressed():
            return
        try:
            occurred_at = datetime.fromtimestamp(record.created, tz=timezone.utc)
            attributes = {
                "level": record.levelname,
                "logger": record.name,
                "message": summarize_trace_data(record.getMessage()),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
                "process_id": record.process,
                "thread_id": record.thread,
            }
            if record.exc_info is not None:
                attributes["exception"] = summarize_trace_data(
                    self.formatter.formatException(record.exc_info)
                    if self.formatter is not None
                    else logging.Formatter().formatException(record.exc_info)
                )
            trace_id = context.legacy_trace_id
            canonical_trace_id = (
                trace_id[6:]
                if trace_id.startswith("trace_") and len(trace_id) == 38
                else trace_id
            )
            with suppress_observation():
                self._wal.submit(
                    ObsRecord(
                        record_type="event",
                        producer_instance_id=self._wal.producer_instance_id,
                        sequence_no=0,
                        occurred_at=occurred_at,
                        trace_id=canonical_trace_id,
                        payload={
                            "session_id": context.conversation_id,
                            "user_id": context.user_id,
                            "tenant_id": context.tenant_id,
                            "name": f"log.{record.name}",
                            "status": "failed" if record.levelno >= logging.ERROR else "completed",
                            "occurred_at": occurred_at.isoformat(),
                            "attributes": attributes,
                        },
                    )
                )
        except Exception:  # noqa: BLE001 - logging must never affect business flow
            self.dropped_count += 1
