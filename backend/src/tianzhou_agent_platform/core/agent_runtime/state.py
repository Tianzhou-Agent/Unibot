"""Reusable agent execution state fields only.

Application extensions (AINA scope, widgets) live in
services/agent_integration/state.py. Do not import conversation or AINA models.
"""

from __future__ import annotations

from typing import Annotated, Any, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from langchain.agents.middleware.types import JumpTo


class RuntimeAgentState(TypedDict, total=False):
    """Native working state shared by execution policies.

    Extends the framework AgentState contract: messages with the native
    reducer, plus optional jump routing. Application fields are added by
    middleware state schemas or the integration package.
    """

    messages: Annotated[list[AnyMessage], add_messages]
    jump_to: JumpTo | None
    structured_response: Any
