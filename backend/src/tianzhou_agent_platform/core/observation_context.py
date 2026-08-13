"""Execution-local observation identity and recursion suppression."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ObservationContext:
    legacy_trace_id: str
    conversation_id: str | None
    user_id: str
    tenant_id: str
    root_span_id: str | None = None


_current_context: ContextVar[ObservationContext | None] = ContextVar(
    "unibot_observation_context", default=None
)
_suppressed: ContextVar[bool] = ContextVar("unibot_observation_suppressed", default=False)


def current_observation_context() -> ObservationContext | None:
    return _current_context.get()


def is_observation_suppressed() -> bool:
    return _suppressed.get()


@contextmanager
def bind_observation_context(context: ObservationContext) -> Iterator[None]:
    token = _current_context.set(context)
    try:
        yield
    finally:
        _current_context.reset(token)


@contextmanager
def suppress_observation() -> Iterator[None]:
    token = _suppressed.set(True)
    try:
        yield
    finally:
        _suppressed.reset(token)
