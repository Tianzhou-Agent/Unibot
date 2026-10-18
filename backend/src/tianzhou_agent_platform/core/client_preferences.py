"""The user's language and time zone, as the shell reports them on each request.

The shell sends ``X-Unibot-Locale`` and ``X-Unibot-Timezone``; the request middleware stores the valid ones here, so
work started by the request (an agent run, its tool calls) can read them. Work without a request, such as a scheduled
run, has neither.
"""

from __future__ import annotations

import re
from contextvars import ContextVar
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

LOCALE_HEADER = "x-unibot-locale"
TIMEZONE_HEADER = "x-unibot-timezone"
_LOCALE = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,3}$")

client_locale: ContextVar[str | None] = ContextVar("client_locale", default=None)
client_timezone: ContextVar[str | None] = ContextVar("client_timezone", default=None)


def remember_client_preferences(locale: str | None, timezone: str | None) -> None:
    """Keep the request's locale and IANA time zone when they are well formed; ignore them otherwise."""
    client_locale.set(locale if locale and len(locale) <= 35 and _LOCALE.match(locale) else None)
    client_timezone.set(timezone if timezone and _known_zone(timezone) else None)


def _known_zone(name: str) -> bool:
    if len(name) > 64 or name.startswith("/") or ".." in name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True
