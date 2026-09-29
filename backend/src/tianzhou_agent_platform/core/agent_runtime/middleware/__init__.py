"""Reusable agent middleware that any ``create_agent`` agent can compose.

- ``model_policy``: final request budget, empty/truncated output guard, malformed tool calls.
- ``provider_policy``: provider error translation and forced tool-choice compatibility.
- ``tool_policy``: ordered tool batches and run-scoped signature deduplication.
- ``approval_policy``: terminal denial of a batch paused for approval.
- ``summarization``: built-in summarization retaining complete user turns.
- ``transcript``: hook that persists messages before summarization replaces them.

Domain knowledge (token estimates, error mappings, archives, texts) is injected by the caller.
"""
