"""Run traces and LLM calls held in memory while the OBS pipeline is on, and how long they are kept."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from tianzhou_agent_platform.observability.models import LLMCallRecord, TraceRecord
from tianzhou_agent_platform.store import repository as repository_module
from tianzhou_agent_platform.store.lifecycle import StorageStores
from tianzhou_agent_platform.store.repository import RUN_TRACE_RETENTION, PersistentRepository
from tests.support.sqlite_store import sqlite_session_factory


class _SqlOnly:
    def __init__(self) -> None:
        self.session_factory = sqlite_session_factory()


def _repository() -> PersistentRepository:
    return PersistentRepository(StorageStores(mysql=_SqlOnly(), redis=None, nas=None))  # type: ignore[arg-type]


def _trace(trace_id: str, minute: int = 0) -> TraceRecord:
    return TraceRecord(
        trace_id=trace_id,
        user_id="u1",
        tenant_id="t1",
        created_at=datetime(2026, 10, 1, tzinfo=UTC) + timedelta(minutes=minute),
    )


def _call(call_id: str, trace_id: str | None) -> LLMCallRecord:
    return LLMCallRecord(
        call_id=call_id,
        trace_id=trace_id,
        endpoint="https://models.invalid/v1",
        model="m",
        request={},
        status="completed",
    )


async def test_finished_runs_leave_memory_after_the_retention_and_unfinished_runs_stay() -> None:
    repository = _repository()
    for trace_id in ("done", "running", "paused", "failed"):
        await repository.create_trace(_trace(trace_id))
    await repository.finish_trace("done", "completed")
    await repository.finish_trace("failed", "failed")
    await repository.finish_trace("paused", "approval_required")
    await repository.upsert_llm_call(_call("call_done", "done"))
    await repository.upsert_llm_call(_call("call_running", "running"))

    await repository._prune_run_traces(datetime.now(UTC))  # noqa: SLF001
    assert set(repository._traces) == {"done", "running", "paused", "failed"}  # noqa: SLF001

    await repository._prune_run_traces(datetime.now(UTC) + RUN_TRACE_RETENTION + timedelta(seconds=1))  # noqa: SLF001
    assert set(repository._traces) == {"running", "paused"}  # noqa: SLF001
    assert set(repository._llm_calls) == {"call_running"}  # noqa: SLF001


async def test_the_oldest_unfinished_runs_are_dropped_beyond_the_cap_but_running_ones_are_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(repository_module, "MAX_RUN_TRACES", 3)
    repository = _repository()
    await repository.create_trace(_trace("running", minute=0))
    for minute in range(1, 5):
        await repository.create_trace(_trace(f"paused{minute}", minute=minute))
        await repository.finish_trace(f"paused{minute}", "approval_required")

    await repository._prune_run_traces(datetime.now(UTC))  # noqa: SLF001

    assert set(repository._traces) == {"running", "paused3", "paused4"}  # noqa: SLF001


async def test_traces_are_not_pruned_when_the_repository_is_their_only_store() -> None:
    repository = PersistentRepository(
        StorageStores(mysql=_SqlOnly(), redis=None, nas=None),  # type: ignore[arg-type]
        persist_observability=True,
    )
    repository._traces["kept"] = _trace("kept").model_copy(  # noqa: SLF001 - seeded without the legacy table
        update={"status": "completed", "completed_at": datetime(2026, 1, 1, tzinfo=UTC)}
    )

    await repository._prune_run_traces(datetime.now(UTC))  # noqa: SLF001

    assert "kept" in repository._traces  # noqa: SLF001
