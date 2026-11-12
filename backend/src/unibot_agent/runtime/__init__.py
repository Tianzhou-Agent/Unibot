"""Agent execution runtime: the ``create_agent`` factory and reusable execution policies.

This package owns framework execution only; application policy reaches it through constructor arguments.
"""

from unibot_agent.runtime.factory import build_agent

__all__ = ["build_agent"]
