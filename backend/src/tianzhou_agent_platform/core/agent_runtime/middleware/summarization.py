"""Working-memory compaction with the built-in ``SummarizationMiddleware``, retaining complete user turns.

The built-in middleware decides when to summarize, generates the summary (with its own retry) and replaces the
summarized messages in the checkpointed state. ``TurnSummarizationMiddleware`` only adds policy it cannot express:

- retention by complete user turns (``keep`` supports message, token and fraction counts only), so a tool call is
  never separated from its result and the current turn is never summarized;
- the trigger is a whole-request threshold: request overhead outside the messages (system prompt, tool schemas)
  counts against it;
- a summary request larger than ``max_summary_input_tokens`` is never sent, and a failed, empty or non-reducing
  summary keeps the unsummarized history for the rest of the invocation instead of failing it (the built-in
  re-raises);
- an optional observer is told when a summary starts, completes or fails.

Summarization replaces messages in the working state; persist originals first (``TranscriptHookMiddleware``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any, Protocol

from langchain.agents.middleware import SummarizationMiddleware
from langchain.agents.middleware.summarization import DEFAULT_SUMMARY_PROMPT
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AnyMessage, BaseMessage, HumanMessage, RemoveMessage

logger = logging.getLogger(__name__)

TokenCounter = Callable[[Iterable[Any]], int]


def is_summary_message(message: Any) -> bool:
    """The summary message ``SummarizationMiddleware`` inserts into the working state."""
    return isinstance(message, BaseMessage) and message.additional_kwargs.get("lc_source") == "summarization"


class SummarizationObserver(Protocol):
    async def started(self, *, summarized_message_count: int) -> None: ...

    async def completed(
        self,
        *,
        before_tokens: int,
        after_tokens: int,
        summarized_message_count: int,
        retained_message_count: int,
    ) -> None: ...

    async def failed(self, error: Exception, *, before_tokens: int) -> None: ...


class TurnSummarizationMiddleware(SummarizationMiddleware):
    def __init__(
        self,
        model: BaseChatModel,
        *,
        threshold_tokens: int,
        min_messages: int,
        keep_turns: int,
        max_summary_input_tokens: int,
        token_counter: TokenCounter,
        overhead_tokens: int = 0,
        summary_prompt: str = DEFAULT_SUMMARY_PROMPT,
        observer: SummarizationObserver | None = None,
    ) -> None:
        super().__init__(
            model,
            trigger={"tokens": max(1, threshold_tokens - overhead_tokens), "messages": min_messages},
            keep=("messages", 1),  # replaced by turn retention in ``_determine_cutoff_index``
            token_counter=token_counter,
            summary_prompt=summary_prompt,
            trim_tokens_to_summarize=None,
        )
        self.keep_turns = keep_turns
        self.max_summary_input_tokens = max_summary_input_tokens
        self.overhead_tokens = overhead_tokens
        self.observer = observer
        self._disabled = False
        self._summarized_count = 0

    def _determine_cutoff_index(self, messages: list[AnyMessage]) -> int:
        """Summarize everything before the ``keep_turns`` most recent user turns."""
        starts = [
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage) and not is_summary_message(message)
        ]
        return starts[-self.keep_turns] if len(starts) > self.keep_turns else 0

    async def _acreate_summary(self, messages_to_summarize: list[AnyMessage]) -> str:
        self._summarized_count = len(messages_to_summarize)
        if self.observer is not None:
            await self.observer.started(summarized_message_count=len(messages_to_summarize))
        if self.token_counter(messages_to_summarize) > self.max_summary_input_tokens:
            raise ValueError("The complete transcript exceeds the compression model input budget")
        summary = await super()._acreate_summary(messages_to_summarize)
        if not summary:
            raise ValueError("The context compression model returned an empty summary")
        return summary

    async def abefore_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:
        if self._disabled:
            return None
        before_tokens = self.token_counter(state["messages"]) + self.overhead_tokens
        try:
            update = await super().abefore_model(state, runtime)
            if update is None:
                return None
            kept = [message for message in update["messages"] if not isinstance(message, RemoveMessage)]
            after_tokens = self.token_counter(kept) + self.overhead_tokens
            if after_tokens >= before_tokens:
                raise ValueError("The context summary did not reduce the estimated request size")
        except Exception as exc:  # noqa: BLE001 - a failed summary keeps the full history
            self._disabled = True
            logger.warning("Context compression failed; preserving the original context", exc_info=True)
            if self.observer is not None:
                await self.observer.failed(exc, before_tokens=before_tokens)
            return None
        if self.observer is not None:
            await self.observer.completed(
                before_tokens=before_tokens,
                after_tokens=after_tokens,
                summarized_message_count=self._summarized_count,
                retained_message_count=len(kept) - 1,
            )
        return update
