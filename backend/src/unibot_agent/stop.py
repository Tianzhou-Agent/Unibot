"""Stop request of a running turn."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any


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
