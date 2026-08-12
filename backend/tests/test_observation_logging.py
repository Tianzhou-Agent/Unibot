from __future__ import annotations

import logging
from pathlib import Path

import pytest

from tianzhou_agent_platform.core.observation_context import (
    ObservationContext,
    bind_observation_context,
    suppress_observation,
)
from tianzhou_agent_platform.core.observation_logging import ObservationLogHandler
from tianzhou_agent_platform.store.observability_wal import (
    WalWriter,
    iter_segment_infos,
    validate_segment_file,
)


@pytest.mark.asyncio
async def test_log_handler_writes_contextual_warning_to_wal(tmp_path: Path) -> None:
    writer = WalWriter(tmp_path, "node-1-abc")
    handler = ObservationLogHandler(writer)
    logger = logging.getLogger("test.observed")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    writer.start()
    context = ObservationContext(
        "trace_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "conv_1",
        "user_1",
        "tenant_1",
        "span_root",
    )
    try:
        with bind_observation_context(context):
            logger.warning("password=secret-value request failed")
        await writer.flush_through(writer.sequence_no)
    finally:
        logger.removeHandler(handler)
        writer.close()
        await writer.wait_closed()

    records = []
    for info in iter_segment_infos(writer._directory):  # type: ignore[attr-defined]
        frames, corrupt, trailing = validate_segment_file(info.path)
        assert corrupt is None and not trailing
        records.extend(frame.record for frame in frames)
    assert len(records) == 1
    event = records[0]
    assert event.record_type == "event"
    assert event.trace_id == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert event.payload["name"] == "log.test.observed"
    assert event.payload["attributes"]["message"] == "password=[REDACTED] request failed"


@pytest.mark.asyncio
async def test_log_handler_honors_recursion_suppression(tmp_path: Path) -> None:
    writer = WalWriter(tmp_path, "node-1-abc")
    handler = ObservationLogHandler(writer)
    logger = logging.getLogger("test.suppressed")
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    writer.start()
    context = ObservationContext("trace_a", None, "user", "tenant", "span_root")
    try:
        with bind_observation_context(context), suppress_observation():
            logger.error("must not recurse")
        assert writer.sequence_no == 0
    finally:
        logger.removeHandler(handler)
        writer.close()
        await writer.wait_closed()
