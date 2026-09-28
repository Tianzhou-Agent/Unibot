"""Agent execution runtime: the ``create_agent`` factory and reusable execution policies.

This package owns framework execution only. It must not import services, conversations, model_providers,
observability, aina, store or api; application policy reaches it through constructor arguments.
"""

from tianzhou_agent_platform.core.agent_runtime.factory import build_agent

__all__ = ["build_agent"]
