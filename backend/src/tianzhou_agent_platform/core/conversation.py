"""Compatibility re-export. Import from tianzhou_agent_platform.conversations.models instead.

Temporary shim with removal gate: Phase 7 after all callers migrate.
"""

from tianzhou_agent_platform.conversations.models import (
    Conversation,
    ConversationCreate,
    ConversationUpdate,
    Message,
)

__all__ = ["Conversation", "ConversationCreate", "ConversationUpdate", "Message"]
