"""Compatibility re-export. Import from tianzhou_agent_platform.observability.query instead.

Temporary shim with removal gate: Phase 7 after all callers migrate.
"""

from tianzhou_agent_platform.observability.query import *  # noqa: F403
from tianzhou_agent_platform.observability.query import _bounded_gzip_decompress  # noqa: F401  (star import skips private names)
