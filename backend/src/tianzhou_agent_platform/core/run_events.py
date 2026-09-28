"""Explicit run-event publisher for chat/trace recording.

Application-layer events (user.request, approval.*, capability.*, final.response)
are published here so core.agent stays free of observability imports.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class RunEventSink(Protocol):
    async def push(
        self,
        trace_id: str,
        *,
        kind: str,
        status: str,
        conversation_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None: ...

    async def start(
        self,
        *,
        trace_id: str,
        conversation_id: str,
        user_id: str,
        tenant_id: str,
        input_data: Any,
    ) -> str | None: ...

    async def finish(self, trace_id: str, status: str) -> None: ...


class RunEventPublisher:
    """Forwards run events to an observation service when one is injected."""

    def __init__(self, backend: Any | None = None) -> None:
        self._backend = backend
        self._root_spans: dict[str, str] = {}
        # Spans this publisher opened; finishing any other id would only log "span not found" in the backend.
        self._open_spans: set[str] = set()

    def root_span_id(self, trace_id: str) -> str | None:
        return self._root_spans.get(trace_id)

    async def push(
        self,
        trace_id: str,
        *,
        kind: str,
        status: str,
        conversation_id: str | None = None,
        details: dict[str, Any] | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        if self._backend is None:
            return
        record = getattr(self._backend, "record_event", None)
        if record is None:
            return
        try:
            await record(
                trace_id,
                kind=kind,
                status=status,
                conversation_id=conversation_id,
                target_type=target_type,
                target_id=target_id,
                duration_ms=duration_ms,
                details=details or {},
            )
        except Exception:  # noqa: BLE001
            return

    async def start_span(self, trace_id: str, **fields: Any) -> None:
        """Open a child span (tool/aina/model) under the run's root span."""
        start = getattr(self._backend, "start_span", None)
        parent = fields.pop("parent_span_id", None) or self._root_spans.get(trace_id)
        if start is None or parent is None:
            return
        try:
            await start(trace_id, parent_span_id=parent, **fields)
        except Exception:  # noqa: BLE001
            return
        self._open_spans.add(str(fields.get("span_id")))

    async def finish_span(self, trace_id: str, span_id: str, status: str, **fields: Any) -> None:
        finish = getattr(self._backend, "finish_span", None)
        if finish is None or span_id not in self._open_spans:
            return
        self._open_spans.discard(span_id)
        try:
            await finish(trace_id, span_id, status, **fields)
        except Exception:  # noqa: BLE001
            return

    async def start(
        self,
        *,
        trace_id: str,
        conversation_id: str,
        user_id: str,
        tenant_id: str,
        input_data: Any,
    ) -> str | None:
        if self._backend is None:
            return None
        from uuid import uuid4

        create = getattr(self._backend, "create_agent_trace", None)
        ensure = getattr(self._backend, "ensure_agent_root_span", None)
        root_span_id = f"span_{uuid4().hex}"
        try:
            if create is not None:
                await create(
                    trace_id=trace_id,
                    root_span_id=root_span_id,
                    conversation_id=conversation_id,
                    user_id=user_id,
                    tenant_id=tenant_id,
                    input_data=input_data,
                    attributes={},
                )
            if ensure is not None:
                root_span_id = await ensure(
                    trace_id,
                    span_id=root_span_id,
                    conversation_id=conversation_id,
                )
        except Exception:  # noqa: BLE001
            pass
        self._root_spans[trace_id] = root_span_id
        self._open_spans.add(root_span_id)
        return root_span_id

    async def resume(self, trace_id: str, *, conversation_id: str) -> str | None:
        """Re-open a run's root span when it continues after an approval (possibly on another instance)."""
        ensure = getattr(self._backend, "ensure_agent_root_span", None)
        if ensure is None:
            return None
        from uuid import uuid4

        try:
            root_span_id = await ensure(trace_id, span_id=f"span_{uuid4().hex}", conversation_id=conversation_id)
        except Exception:  # noqa: BLE001
            return None
        self._root_spans[trace_id] = root_span_id
        self._open_spans.add(root_span_id)
        return root_span_id

    async def finish(self, trace_id: str, status: str) -> None:
        self._root_spans.pop(trace_id, None)
        if self._backend is None:
            return
        finish = getattr(self._backend, "finish_trace", None)
        if finish is None:
            return
        try:
            await finish(trace_id, status)
        except Exception:  # noqa: BLE001
            return


NULL_RUN_EVENTS = RunEventPublisher(None)
