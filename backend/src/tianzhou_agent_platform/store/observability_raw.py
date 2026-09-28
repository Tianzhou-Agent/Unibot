"""Atomic raw IO persistence: redact -> JSON -> gzip -> tmp -> fsync -> rename.

Design (section 8 of ir-01): full model/tool IO is stored under
``<raw_root>/<tenant_id>/<user_id>/<trace_id>/<span_id>.json.gz`` after
redaction. The file must be durably persisted *before* its reference is
allowed into a durable-buffer record or the database, so a crash never leaves a
database reference to a missing file. Orphaned ``.tmp`` files are possible
after crashes and are cleaned by a periodic task.

Path components are validated system IDs; model/user-provided names are
never used in paths.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tianzhou_agent_platform.core.trace_details import redact_trace_data

logger = logging.getLogger(__name__)

TMP_SUFFIX = ".tmp"

_ID_COMPONENT_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")

RawIoStatus = Literal["ready", "failed", "too_large", "not_applicable"]


@dataclass(slots=True)
class RawIoRef:
    """Reference to a persisted raw IO file, safe to put into a Span record."""

    status: RawIoStatus
    path: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None

    def to_span_attributes(self) -> dict[str, Any]:
        return {
            "unibot.raw_io.path": self.path,
            "unibot.raw_io.sha256": self.sha256,
            "unibot.raw_io.size_bytes": self.size_bytes,
            "unibot.raw_io.status": self.status,
        }


def validate_id_component(value: str, *, label: str) -> str:
    if not _ID_COMPONENT_RE.match(value):
        raise ValueError(f"invalid {label}: {value!r}")
    return value


class RawIoWriter:
    def __init__(self, raw_root: Path, max_file_size_bytes: int = 100 * 1024 * 1024) -> None:
        self._raw_root = raw_root.resolve(strict=False)
        self._max_file_size_bytes = max_file_size_bytes

    @property
    def raw_root(self) -> Path:
        return self._raw_root

    async def write(
        self,
        *,
        kind: Literal["model", "tool", "aina", "internal"],
        trace_id: str,
        span_id: str,
        tenant_id: str,
        user_id: str,
        data: dict[str, Any],
    ) -> RawIoRef:
        """Persist one redacted IO payload atomically. Never raises for content issues."""
        validate_id_component(trace_id, label="trace_id")
        validate_id_component(span_id, label="span_id")
        validate_id_component(tenant_id, label="tenant_id")
        validate_id_component(user_id, label="user_id")
        document = {
            "schema_version": 1,
            "kind": kind,
            "trace_id": trace_id,
            "span_id": span_id,
            **redact_trace_data(data),
        }
        try:
            raw = json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            logger.warning("raw IO payload is not JSON-serializable: %s", exc)
            return RawIoRef(status="failed")
        compressed = gzip.compress(raw, compresslevel=6)
        if len(compressed) > self._max_file_size_bytes:
            logger.warning(
                "raw IO too large: %d bytes compressed (limit %d); marking too_large",
                len(compressed),
                self._max_file_size_bytes,
            )
            return RawIoRef(status="too_large")
        directory = self._raw_root / tenant_id / user_id / trace_id
        target = directory / f"{span_id}.json.gz"
        tmp = directory / f"{span_id}.json.gz{TMP_SUFFIX}"
        try:
            await asyncio.to_thread(self._write_atomic, tmp, target, compressed)
        except OSError as exc:
            logger.exception("raw IO atomic write failed: %s", exc)
            return RawIoRef(status="failed")
        digest = hashlib.sha256(compressed).hexdigest()
        return RawIoRef(
            status="ready",
            path=target.relative_to(self._raw_root).as_posix(),
            sha256=digest,
            size_bytes=len(compressed),
        )

    def _write_atomic(self, tmp: Path, target: Path, content: bytes) -> None:
        tmp.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp, target)
        _fsync_directory(target.parent)


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
