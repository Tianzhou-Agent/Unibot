"""RawIoWriter tests: atomic write, redaction, too_large, path validation."""

from __future__ import annotations

import gzip
import json
import secrets
from pathlib import Path

import pytest

from tianzhou_agent_platform.store.observability_raw import RawIoWriter, validate_id_component


@pytest.fixture
def writer(tmp_path: Path) -> RawIoWriter:
    return RawIoWriter(tmp_path / "raw", max_file_size_bytes=10_000)


@pytest.mark.asyncio
async def test_atomic_write_and_readback(writer: RawIoWriter) -> None:
    ref = await writer.write(
        kind="model",
        trace_id="trace_abc123",
        span_id="span_abc123",
        tenant_id="tenant_1",
        user_id="user_1",
        data={"request": {"messages": [{"role": "user", "content": "hello"}]}, "response": {"content": "hi"}},
    )
    assert ref.status == "ready"
    assert ref.sha256 and len(ref.sha256) == 64
    assert ref.size_bytes and ref.size_bytes > 0
    target = writer.raw_root / ref.path  # type: ignore[operator]
    assert target.exists()
    with gzip.open(target, "rt", encoding="utf-8") as file:
        document = json.load(file)
    assert document["schema_version"] == 1
    assert document["kind"] == "model"
    assert document["trace_id"] == "trace_abc123"
    assert document["request"]["messages"][0]["content"] == "hello"
    # no leftover temp files
    assert list(target.parent.glob("*.tmp")) == []


@pytest.mark.asyncio
async def test_redaction_before_persist(writer: RawIoWriter) -> None:
    ref = await writer.write(
        kind="tool",
        trace_id="trace_abc123",
        span_id="span_abc123",
        tenant_id="tenant_1",
        user_id="user_1",
        data={
            "input": {"api_key": "sk-super-secret-value", "message": "Bearer abcdef123456"},
            "output": {"ok": True},
        },
    )
    target = writer.raw_root / ref.path  # type: ignore[operator]
    with gzip.open(target, "rt", encoding="utf-8") as file:
        document = json.load(file)
    assert document["input"]["api_key"] == "[REDACTED]"
    assert "sk-super-secret-value" not in json.dumps(document)
    assert "abcdef123456" not in json.dumps(document)


@pytest.mark.asyncio
async def test_redaction_covers_camel_case_keys(writer: RawIoWriter) -> None:
    ref = await writer.write(
        kind="tool",
        trace_id="trace_abc123",
        span_id="span_abc123",
        tenant_id="tenant_1",
        user_id="user_1",
        data={
            "input": {
                "accessToken": "jwt-secret-value",
                "apiKey": "key-value",
                "refreshToken": "refresh-value",
                "safeField": "visible",
            },
        },
    )
    target = writer.raw_root / ref.path  # type: ignore[operator]
    with gzip.open(target, "rt", encoding="utf-8") as file:
        document = json.load(file)
    assert document["input"]["accessToken"] == "[REDACTED]"
    assert document["input"]["apiKey"] == "[REDACTED]"
    assert document["input"]["refreshToken"] == "[REDACTED]"
    assert document["input"]["safeField"] == "visible"
    serialized = json.dumps(document)
    assert "jwt-secret-value" not in serialized
    assert "refresh-value" not in serialized


@pytest.mark.asyncio
async def test_too_large_marked_not_faked(writer: RawIoWriter) -> None:
    ref = await writer.write(
        kind="model",
        trace_id="trace_abc123",
        span_id="span_abc123",
        tenant_id="tenant_1",
        user_id="user_1",
        data={"request": {"big": secrets.token_hex(25_000)}},  # incompressible, exceeds 10 KB gzipped
    )
    assert ref.status == "too_large"
    assert ref.path is None
    assert not (writer.raw_root / "tenant_1" / "user_1" / "trace_abc123").exists()


@pytest.mark.asyncio
async def test_path_components_are_validated(writer: RawIoWriter) -> None:
    with pytest.raises(ValueError):
        validate_id_component("../../etc/passwd", label="trace_id")
    with pytest.raises(ValueError):
        validate_id_component("", label="user_id")
    with pytest.raises(ValueError):
        validate_id_component("a b c", label="span_id")


@pytest.mark.asyncio
async def test_nested_directories_layout(writer: RawIoWriter) -> None:
    ref = await writer.write(
        kind="aina",
        trace_id="trace_abc123",
        span_id="span_abc123",
        tenant_id="tenant_42",
        user_id="user_9",
        data={"request": {}, "response": {}},
    )
    assert ref.path == "tenant_42/user_9/trace_abc123/span_abc123.json.gz"  # type: ignore[operator]


@pytest.mark.asyncio
async def test_redaction_handles_non_string_keys(writer: RawIoWriter) -> None:
    """Non-string mapping keys must not crash redaction (raw IO keeps working)."""
    ref = await writer.write(
        kind="tool",
        trace_id="trace_abc123",
        span_id="span_abc123",
        tenant_id="tenant_1",
        user_id="user_1",
        data={1: "one", 2.5: "two", ("api_key",): "tuple-key"},
    )
    assert ref.status == "ready"
    target = writer.raw_root / ref.path  # type: ignore[operator]
    with gzip.open(target, "rt", encoding="utf-8") as file:
        document = json.load(file)
    assert document["1"] == "one"
    assert "tuple-key" not in json.dumps(document)


@pytest.mark.asyncio
async def test_non_serializable_payload_degraded_gracefully(writer: RawIoWriter) -> None:
    ref = await writer.write(
        kind="model",
        trace_id="trace_abc123",
        span_id="span_abc123",
        tenant_id="tenant_1",
        user_id="user_1",
        data={"request": {"bad": object()}},
    )
    # redaction degrades unknown objects to strings, so the file is still
    # JSON-serializable; no exception escapes into the business path
    assert ref.status == "ready"
    target = writer.raw_root / ref.path  # type: ignore[operator]
    with gzip.open(target, "rt", encoding="utf-8") as file:
        json.load(file)
