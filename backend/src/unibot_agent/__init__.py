"""Agent core: how an agent turn works.

The core reaches everything else (sessions, models, tools, memory, tracing) through the interfaces it declares
in ``unibot_agent.ports``. It imports only the standard library, pydantic and LangChain/LangGraph, never the
backend (``tianzhou_agent_platform``); ``tests/test_service_boundaries.py`` enforces this.
"""
