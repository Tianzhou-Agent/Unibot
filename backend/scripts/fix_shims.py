"""Rewrite corrupted compatibility shims."""
from pathlib import Path

ROOT = Path("src/tianzhou_agent_platform/core")
MAP = {
    "observability.py": "observability.service",
    "observability_query.py": "observability.query",
    "observability_stream.py": "observability.stream",
    "observability_writer.py": "observability.writer",
    "observation_context.py": "observability.context",
    "observation_logging.py": "observability.logging",
    "telemetry.py": "observability.telemetry",
    "trace_details.py": "observability.trace_details",
    "operations_analytics.py": "observability.analytics",
}

for name, target in MAP.items():
    body = (
        f'"""Compatibility re-export. Import from tianzhou_agent_platform.{target} instead.\n\n'
        "Temporary shim with removal gate: Phase 7 after all callers migrate.\n"
        '"""\n\n'
        f"from tianzhou_agent_platform.{target} import *  # noqa: F403\n"
    )
    (ROOT / name).write_text(body, encoding="utf-8")
    print("fixed", name)
