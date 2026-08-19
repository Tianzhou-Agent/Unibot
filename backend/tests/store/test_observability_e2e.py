"""End-to-end pipeline test: real create_app startup
-> chat request -> OTel/Redis Streams -> ingest -> MySQL -> /obs queries.

This closes the loop the unit tests cannot: it proves the production wiring
(main.py passing a Tracer, root span sharing the trace id with buffered trace
records, token aggregation, barrier) actually works. Requires MySQL/Redis on
the default local ports; skipped otherwise.
"""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from redis import Redis

from tianzhou_agent_platform.config import AgentSettings
from tianzhou_agent_platform.main import create_app
from tianzhou_agent_platform.store.settings import StorageSettings

from tests.support.fake_llm import ScriptedLLM, assistant

MYSQL_DSN = os.getenv("OBS_TEST_MYSQL_DSN", "")
REDIS_URL = os.getenv("OBS_TEST_REDIS_URL", "redis://127.0.0.1:16379/0")


def _services_available() -> bool:
    if not MYSQL_DSN:
        return False
    for host_port in ("127.0.0.1:13306", "127.0.0.1:16379"):
        host, port = host_port.split(":")
        try:
            with socket.create_connection((host, int(port)), timeout=2):
                pass
        except OSError:
            return False
    return True


pytestmark = pytest.mark.skipif(not _services_available(), reason="MySQL/Redis are not reachable for OBS e2e tests")


def _wait_for(predicate, timeout: float = 10.0, interval: float = 0.3) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _delete_redis_keys(keys: list[str]) -> None:
    client = Redis.from_url(REDIS_URL)
    try:
        client.delete(*keys)
    finally:
        client.close()


@pytest.mark.asyncio
async def test_chat_to_obs_query_end_to_end(
    tmp_path: Path,
    request: pytest.FixtureRequest,
) -> None:
    from tianzhou_agent_platform.store.observability_store import OBS_METADATA, ObservabilityStore

    # clean OBS tables so the overview assertions are deterministic
    cleanup = ObservabilityStore.from_dsn(MYSQL_DSN)
    async with cleanup._engine.begin() as connection:  # noqa: SLF001
        await connection.run_sync(OBS_METADATA.drop_all)
        await connection.run_sync(OBS_METADATA.create_all)
    await cleanup.close()

    redis_prefix = f"unibot:test:obs:{uuid4().hex}"
    redis_keys = [redis_prefix, f"{redis_prefix}:dlq", f"{redis_prefix}:producers"]
    request.addfinalizer(lambda: _delete_redis_keys(redis_keys))
    settings = AgentSettings(
        llm_base_url=None,
        llm_api_key=None,
        llm_model="test-model",
        node_id="e2e-node",
        obs_wal_root=tmp_path / "wal",
        obs_raw_root=tmp_path / "raw",
        obs_enabled=True,
        obs_redis_stream_key=redis_keys[0],
        obs_redis_group_name=f"unibot-test-{uuid4().hex}",
        obs_redis_dlq_key=redis_keys[1],
        obs_redis_producers_key=redis_keys[2],
    )
    storage = StorageSettings(
        mysql_dsn=MYSQL_DSN,
        redis_dsn=REDIS_URL,
        nas_root_path=tmp_path / "nas",
    )
    app = create_app(settings=settings, storage_settings=storage, llm=ScriptedLLM([assistant("你好，我是 Unibot。")]))

    with TestClient(app) as client:
        # 1) chat request completes (barrier ran before the response returned)
        chat = client.post("/chat", json={"message": "你好"})
        assert chat.status_code == 200, chat.text
        conversation_id = chat.json()["conversation_id"]
        assert conversation_id

        # 2) /obs session detail becomes visible after ingest (final consistency)
        def session_ready() -> bool:
            response = client.get(f"/obs/sessions/{conversation_id}")
            if response.status_code != 200 or response.json() is None:
                return False
            body = response.json()
            return bool(body["traces"]) and bool(body["spans"])

        assert _wait_for(session_ready), "OBS session detail never became visible"

        detail = client.get(f"/obs/sessions/{conversation_id}").json()
        trace = detail["traces"][0]
        assert trace["status"] == "completed"
        # P0-2: span rows share the trace id with the trace row
        span_trace_ids = {span["trace_id"] for span in detail["spans"]}
        assert span_trace_ids == {trace["trace_id"]}, "span/trace trace_id mismatch"
        # P1-4: token totals are aggregated (root span was ended before finish_trace)
        assert trace["input_tokens"] == 5
        assert trace["output_tokens"] == 3
        # a model span exists with the raw IO persisted
        model_spans = [span for span in detail["spans"] if span["kind"] == "model"]
        assert model_spans, "no model span in session detail"

        # 3) personal overview aggregates from the Trace table
        overview = client.get("/obs/overview?range=week").json()
        assert overview["trace_count"] >= 1
        assert overview["total_tokens"] == 8  # 5 input + 3 output, no double count
