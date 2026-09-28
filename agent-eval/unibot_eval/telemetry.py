"""OpenTelemetry instrumentation for the eval harness itself.

Every eval run produces a span tree ``eval.run -> eval.case -> eval.attempt -> eval.turn`` with the scores as
attributes and one span event per check. Each ``eval.turn`` span carries ``unibot.trace_id`` so an eval result
can be joined to the backend's own OTel trace, and the harness injects a W3C ``traceparent`` header into every
request it sends to the backend.

Exporters:
  * ``OTEL_EXPORTER_OTLP_ENDPOINT`` set  -> OTLP/HTTP (needs the ``otlp`` extra)
  * ``UNIBOT_EVAL_OTEL_CONSOLE=1``       -> console
  * otherwise                            -> spans are created but not exported
"""

from __future__ import annotations

import logging
import os
from typing import Any

from opentelemetry import propagate, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SimpleSpanProcessor, SpanExporter

logger = logging.getLogger(__name__)

SERVICE_NAME = "unibot-agent-eval"
_provider: TracerProvider | None = None


def setup_tracing(exporter: SpanExporter | None = None) -> TracerProvider:
    """Create the harness TracerProvider. Pass ``exporter`` to capture spans in tests."""
    global _provider
    if _provider is not None and exporter is None:
        return _provider
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME}))
    if exporter is not None:
        provider.add_span_processor(SimpleSpanProcessor(exporter))
    else:
        for configured in _configured_exporters():
            provider.add_span_processor(BatchSpanProcessor(configured))
    _provider = provider
    return provider


def tracer() -> trace.Tracer:
    return setup_tracing().get_tracer("unibot_eval")


def shutdown_tracing() -> None:
    global _provider
    if _provider is not None:
        _provider.force_flush()
        _provider.shutdown()
        _provider = None


def inject_trace_headers(headers: dict[str, str]) -> dict[str, str]:
    propagate.inject(headers)
    return headers


def set_attributes(span: trace.Span, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if value is None:
            continue
        if isinstance(value, (str, bool, int, float)):
            span.set_attribute(key, value)
        elif isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
            span.set_attribute(key, list(value))
        else:
            span.set_attribute(key, str(value))


def _configured_exporters() -> list[SpanExporter]:
    exporters: list[SpanExporter] = []
    if os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"):
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        except ImportError:
            logger.warning("OTLP endpoint configured but the 'otlp' extra is not installed; spans are not exported")
        else:
            exporters.append(OTLPSpanExporter())
    if os.getenv("UNIBOT_EVAL_OTEL_CONSOLE", "").lower() in {"1", "true", "yes"}:
        exporters.append(ConsoleSpanExporter())
    return exporters
