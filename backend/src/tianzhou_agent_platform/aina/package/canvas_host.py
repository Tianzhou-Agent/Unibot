"""Headless canvas host (design §9.5): canvas tools keep working while no tab shows the conversation.

Canvas tools run in a canvas the shell shows. So that a run keeps working after the user closes the tab, the
platform opens the shell's canvas-host page for the conversation in a server-side headless browser, signed in as the
conversation's owner. The page joins the conversation as a background viewer: a call goes to it only when no tab
takes the call, and it is closed once a tab takes a call again or after it has been idle for a while. A canvas that
must keep the work saves it itself (design §9.5 "Persistence is the canvas's job"), so the tab the user opens later
shows it.

Playwright runs on its own thread and event loop: it starts the browser as a subprocess, which the API's event loop
may not support (a selector loop on Windows).
"""

from __future__ import annotations

import asyncio
import logging
import sys
import threading
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Coroutine
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, TypeVar
from urllib.parse import quote, urlsplit
from uuid import uuid4

logger = logging.getLogger(__name__)

T = TypeVar("T")

HEADLESS_CLIENT_PREFIX = "headless_"
IDLE_SECONDS = 300.0
_REAP_INTERVAL_SECONDS = 30.0
_SESSION_COOKIE = "unibot_session"


def is_headless(client_id: str) -> bool:
    return client_id.startswith(HEADLESS_CLIENT_PREFIX)


@dataclass
class _Page:
    client_id: str
    context: Any
    used_at: float


class HeadlessCanvasHost:
    """One headless canvas-host page per conversation, opened on demand.

    ``session_token`` gives a session token of the conversation's owner (None when the shell needs none, as with the
    development auth bypass).
    """

    def __init__(
        self,
        *,
        frontend_base_url: str,
        session_token: Callable[[str], Awaitable[str | None]],
        idle_seconds: float = IDLE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = frontend_base_url.rstrip("/")
        self._session_token = session_token
        self._idle_seconds = idle_seconds
        self._clock = clock
        self._pages: dict[str, _Page] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._browser: Any | None = None
        self._playwright: Any | None = None
        self._reaper: asyncio.Task[None] | None = None

    async def open(self, conversation_id: str, aina_id: str) -> str | None:
        """Client id of the conversation's headless viewer, opening its page first if needed.

        None when no headless browser can be started; the call then fails as it does without one.
        """
        async with self._locks[conversation_id]:
            page = self._pages.get(conversation_id)
            if page is not None:
                page.used_at = self._clock()
                return page.client_id
            token = await self._session_token(conversation_id)
            client_id = f"{HEADLESS_CLIENT_PREFIX}{uuid4().hex}"
            url = (
                f"{self._base}/canvas-host/{quote(conversation_id, safe='')}"
                f"?client_id={client_id}&aina={quote(aina_id, safe='')}"
            )
            try:
                context = await self._in_browser(self._new_page(url, token))
            except Exception:
                logger.warning("headless canvas host could not open %s", conversation_id, exc_info=True)
                return None
            logger.info("headless canvas host opened %s as %s", conversation_id, client_id)
            self._pages[conversation_id] = _Page(client_id, context, self._clock())
            if self._reaper is None or self._reaper.done():
                self._reaper = asyncio.create_task(self._reap())
            return client_id

    async def close(self, conversation_id: str) -> None:
        async with self._locks[conversation_id]:
            page = self._pages.pop(conversation_id, None)
            if page is None:
                return
            logger.info("headless canvas host closed %s", conversation_id)
            with suppress(Exception):
                await self._in_browser(page.context.close())

    async def aclose(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            await asyncio.gather(self._reaper, return_exceptions=True)
        for conversation_id in list(self._pages):
            await self.close(conversation_id)
        if self._loop is None:
            return
        with suppress(Exception):
            await self._in_browser(self._stop_browser())
        self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None:
            await asyncio.to_thread(self._thread.join, 5)
        self._loop = self._thread = None

    async def _reap(self) -> None:
        """Close the pages no call has used for ``idle_seconds``."""
        while self._pages:
            await asyncio.sleep(_REAP_INTERVAL_SECONDS)
            now = self._clock()
            for conversation_id, page in list(self._pages.items()):
                if now - page.used_at >= self._idle_seconds:
                    await self.close(conversation_id)

    # --- Browser thread -----------------------------------------------------------------------------------------

    async def _in_browser(self, operation: Coroutine[Any, Any, T]) -> T:
        if self._loop is None:
            loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
            self._thread = threading.Thread(target=loop.run_forever, name="headless-canvas-host", daemon=True)
            self._thread.start()
            self._loop = loop
        return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(operation, self._loop))

    async def _new_page(self, url: str, token: str | None) -> Any:
        if self._browser is None or not self._browser.is_connected():
            from playwright.async_api import async_playwright

            if self._playwright is None:
                self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=True)
        context = await self._browser.new_context(viewport={"width": 1440, "height": 900})
        try:
            if token is not None:
                origin = urlsplit(self._base)
                cookie = {"name": _SESSION_COOKIE, "value": token, "domain": origin.hostname or "", "path": "/"}
                await context.add_cookies([{**cookie, "secure": origin.scheme == "https"}])
            page = await context.new_page()
            page.on("console", lambda message: _log_console(url, message))
            await page.goto(url, wait_until="domcontentloaded")
        except BaseException:
            await context.close()
            raise
        return context

    async def _stop_browser(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()
        self._browser = self._playwright = None


def _log_console(url: str, message: Any) -> None:
    if message.type == "error":
        logger.info("headless canvas host %s: %s", url, message.text)
