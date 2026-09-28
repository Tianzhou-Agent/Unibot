"""Compatibility re-export. Import from tianzhou_agent_platform.services.platform_tools instead.

Temporary shim with removal gate: Phase 7 after all callers migrate.
"""

from tianzhou_agent_platform.services.platform_tools import *  # noqa: F403
from tianzhou_agent_platform.services.platform_tools import (  # noqa: F401
    DESCRIBE_AINA_TOOL_ID,
    LIST_APP_TOOL_ID,
    OPEN_AINA_TOOL_ID,
    PLATFORM_TOOL_IDS,
    REQUEST_CLARIFICATION_TOOL_ID,
    invoke_platform_tool,
    list_app_widget,
    open_aina,
)
