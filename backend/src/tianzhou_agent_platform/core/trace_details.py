from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

REDACTED = "[REDACTED]"
_MAX_DEPTH = 8
_MAX_ITEMS = 50
_MAX_STRING_LENGTH = 4_000
# Original-IO redaction must not truncate; only a defensive recursion cap is
# applied so malformed cyclic structures cannot hang the writer.
_REDACT_MAX_DEPTH = 64
_SENSITIVE_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "credential",
    "credentials",
    "id_token",
    "password",
    "passwd",
    "proxy_authorization",
    "refresh_token",
    "secret",
    "set_cookie",
    "access_token",
}
_SENSITIVE_SUFFIXES = (
    "_api_key",
    "_credential",
    "_credentials",
    "_password",
    "_secret",
    "_token",
)
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_OPENAI_STYLE_KEY_RE = re.compile(r"\bsk-[a-zA-Z0-9_-]{8,}\b")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(password|passwd|api[ _-]?key|access[ _-]?token|refresh[ _-]?token|secret)\b"
    r"(\s*(?:=|:|\bis\b)\s*)([^\s,;]+)"
)
_CHINESE_SECRET_ASSIGNMENT_RE = re.compile(r"(密码|密钥|令牌)(\s*(?:是|=|：|:)\s*)([^\s，,；;]+)")


def redact_trace_data(value: Any, *, _depth: int = 0) -> Any:
    """Remove secrets without truncating; used for complete raw IO.

    Design section 8.4: redaction happens before anything enters WAL or raw
    files; summary limits are a separate concern handled by
    ``summarize_trace_data``.
    """
    if _depth >= _REDACT_MAX_DEPTH:
        return "[MAX_DEPTH]"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if _is_sensitive_key(str(key)) else redact_trace_data(item, _depth=_depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [redact_trace_data(item, _depth=_depth + 1) for item in value]
    if isinstance(value, bytes):
        return f"[BINARY {len(value)} BYTES]"
    if isinstance(value, str):
        return _redact_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _redact_text(str(value))


def summarize_trace_data(value: Any, *, _depth: int = 0) -> Any:
    """Redact first, then apply depth/quantity/string limits (Span previews)."""
    if _depth >= _MAX_DEPTH:
        return "[MAX_DEPTH]"
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Mapping):
        sanitized: dict[str, Any] = {}
        items = list(value.items())
        for raw_key, item in items[:_MAX_ITEMS]:
            key = str(raw_key)
            sanitized[key] = REDACTED if _is_sensitive_key(key) else summarize_trace_data(item, _depth=_depth + 1)
        if len(items) > _MAX_ITEMS:
            sanitized["__truncated_items__"] = len(items) - _MAX_ITEMS
        return sanitized
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = list(value)
        sanitized_items = [summarize_trace_data(item, _depth=_depth + 1) for item in items[:_MAX_ITEMS]]
        if len(items) > _MAX_ITEMS:
            sanitized_items.append(f"[TRUNCATED {len(items) - _MAX_ITEMS} ITEMS]")
        return sanitized_items
    if isinstance(value, bytes):
        return f"[BINARY {len(value)} BYTES]"
    if isinstance(value, str):
        return _truncate(_redact_text(value))
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _truncate(_redact_text(str(value)))


def sanitize_trace_data(value: Any, *, _depth: int = 0) -> Any:
    """Backward-compatible alias for ``summarize_trace_data``."""
    return summarize_trace_data(value, _depth=_depth)


def _is_sensitive_key(key: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    compact = normalized.replace("_", "")
    if normalized in _SENSITIVE_KEYS or compact in _SENSITIVE_KEYS:
        return True
    # camelCase / 无分隔符变体（accessToken / apiKey / refreshToken）
    if compact in {item.replace("_", "") for item in _SENSITIVE_KEYS}:
        return True
    if normalized.endswith(_SENSITIVE_SUFFIXES):
        return True
    return any(compact.endswith(suffix.replace("_", "")) for suffix in _SENSITIVE_SUFFIXES)


def _redact_text(value: str) -> str:
    redacted = _BEARER_RE.sub("Bearer [REDACTED]", value)
    redacted = _OPENAI_STYLE_KEY_RE.sub(REDACTED, redacted)
    redacted = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}", redacted)
    return _CHINESE_SECRET_ASSIGNMENT_RE.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        redacted,
    )


def _truncate(value: str) -> str:
    if len(value) <= _MAX_STRING_LENGTH:
        return value
    return f"{value[:_MAX_STRING_LENGTH]}...[TRUNCATED {len(value) - _MAX_STRING_LENGTH} CHARS]"
