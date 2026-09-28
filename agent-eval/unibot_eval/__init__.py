"""Black-box evaluation harness for the Unibot agent.

The harness drives the backend only through its public REST API (``/chat``, ``/chat/stream``,
``/traces/{id}``, ``/llm-calls``, ``/approvals``, ``/conversations``). It reads the OpenTelemetry-derived
trace the backend already records for every run, scores behaviour (tool use, arguments, context handling,
safety) and performance (latency, TTFT, tokens, iterations), and emits its own OpenTelemetry spans.
"""

__version__ = "0.1.0"
