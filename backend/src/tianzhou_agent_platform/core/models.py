"""Compatibility exports for the pre-refactor model import path.

New code should import models from their owning domain module.
"""

from tianzhou_agent_platform.aina.memory.models import (
    MemoryCategory,
    MemoryCreate,
    MemoryListResponse,
    MemoryRecord,
    MemoryStats,
    MemoryUpdate,
)
from tianzhou_agent_platform.aina.protocol.models import (
    AinaCapabilities,
    AinaCapability,
    AinaCanvasResponse,
    AinaIdentity,
    AinaInstallation,
    AinaInvokeRequest,
    AinaInvokeResponse,
    AinaManifest,
    AinaOutput,
    AinaRecord,
    AinaUiCapability,
    BuiltinRuntimeDefinition,
    InstallationRequest,
    OpenAinaRequest,
    PermissionUpdate,
    Publisher,
    RemoteRuntimeDefinition,
)
from tianzhou_agent_platform.aina.protocol.widgets import (
    WidgetAction,
    WidgetApp,
    WidgetDefinition,
    WidgetField,
)
from tianzhou_agent_platform.aina.security.models import Authentication
from tianzhou_agent_platform.aina.skill.models import SkillCreate, SkillRecord
from tianzhou_agent_platform.aina.tool.models import ToolCreate, ToolRecord
from tianzhou_agent_platform.core.base import StrictModel, Usage, utc_now
from tianzhou_agent_platform.core.chat import (
    ApprovalAction,
    ApprovalRecord,
    ChatRequest,
    ChatResponse,
    ErrorEnvelope,
    StandardError,
    TraceEvent,
    TraceRecord,
    TraceSpan,
)
from tianzhou_agent_platform.core.conversation import Conversation, ConversationCreate, ConversationUpdate, Message

__all__ = [
    "AinaCapabilities",
    "AinaCapability",
    "AinaCanvasResponse",
    "AinaIdentity",
    "AinaInstallation",
    "AinaInvokeRequest",
    "AinaInvokeResponse",
    "AinaManifest",
    "AinaOutput",
    "AinaRecord",
    "AinaUiCapability",
    "ApprovalAction",
    "ApprovalRecord",
    "Authentication",
    "BuiltinRuntimeDefinition",
    "ChatRequest",
    "ChatResponse",
    "Conversation",
    "ConversationCreate",
    "ConversationUpdate",
    "ErrorEnvelope",
    "InstallationRequest",
    "MemoryCategory",
    "MemoryCreate",
    "MemoryListResponse",
    "MemoryRecord",
    "MemoryStats",
    "MemoryUpdate",
    "Message",
    "OpenAinaRequest",
    "PermissionUpdate",
    "Publisher",
    "RemoteRuntimeDefinition",
    "SkillCreate",
    "SkillRecord",
    "StandardError",
    "StrictModel",
    "ToolCreate",
    "ToolRecord",
    "TraceEvent",
    "TraceRecord",
    "TraceSpan",
    "Usage",
    "WidgetAction",
    "WidgetApp",
    "WidgetDefinition",
    "WidgetField",
    "utc_now",
]
