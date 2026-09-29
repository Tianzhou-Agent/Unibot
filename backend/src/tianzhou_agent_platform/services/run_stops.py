"""User stop requests for running turns.

A stop request reaches the process running the conversation's turn: directly when it runs in this process, else
over Redis pub/sub. It cancels only the turn's agent execution; LangGraph keeps every checkpointed step, so the turn
ends as ``stopped`` and can be resumed from its checkpoint.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Coroutine
from contextlib import asynccontextmanager, suppress
from typing import Any

from tianzhou_agent_platform.store.errors import StorageError
from tianzhou_agent_platform.store.redis import RedisStore

STOP_CHANNEL = "conversation-run-stop"


class TurnStop:
    """Stop request of one turn; ``run`` executes the part of the turn a stop may cancel."""

    def __init__(self) -> None:
        self.requested = False
        self._execution: asyncio.Future[Any] | None = None

    def request(self) -> None:
        self.requested = True
        if self._execution is not None:
            self._execution.cancel()

    async def run(self, execution: Coroutine[Any, Any, Any]) -> bool:
        """Await ``execution``; False when a stop request ended it (or came before it started)."""
        if self.requested:
            execution.close()
            return False
        self._execution = asyncio.ensure_future(execution)
        try:
            await self._execution
        except asyncio.CancelledError:
            # Only a stop of the execution itself is absorbed; cancelling the turn still propagates.
            current = asyncio.current_task()
            if self.requested and (current is None or not current.cancelling()):
                return False
            raise
        finally:
            self._execution = None
        return True


class RunStops:
    """Delivers stop requests to the turns running in any process."""

    def __init__(self, redis: RedisStore | None = None) -> None:
        self._redis = redis
        self._local: dict[str, tuple[str, TurnStop]] = {}

    async def request(self, conversation_id: str, trace_id: str) -> None:
        local = self._local.get(conversation_id)
        if local is not None and local[0] == trace_id:
            local[1].request()
            return
        if self._redis is not None:
            await self._redis.publish(STOP_CHANNEL, conversation_id, {"trace_id": trace_id})

    @asynccontextmanager
    async def watch(self, conversation_id: str, trace_id: str) -> AsyncIterator[TurnStop]:
        """The stop request of a turn this process runs, for as long as the context is open."""
        stop = TurnStop()
        self._local[conversation_id] = (trace_id, stop)
        redis_context: Any | None = None
        relay_task: asyncio.Task[None] | None = None
        if self._redis is not None:
            try:
                redis_context = self._redis.subscribe(STOP_CHANNEL, conversation_id)
                messages = await redis_context.__aenter__()

                async def relay() -> None:
                    try:
                        async for value in messages:
                            if isinstance(value, dict) and value.get("trace_id") == trace_id:
                                stop.request()
                    except StorageError:
                        return

                relay_task = asyncio.create_task(relay())
            except StorageError:
                # Without Redis the turn can still be stopped from this process.
                redis_context = None
        try:
            yield stop
        finally:
            if relay_task is not None:
                relay_task.cancel()
                with suppress(asyncio.CancelledError):
                    await relay_task
            if redis_context is not None:
                await redis_context.__aexit__(None, None, None)
            if self._local.get(conversation_id, (None,))[0] == trace_id:
                self._local.pop(conversation_id, None)
