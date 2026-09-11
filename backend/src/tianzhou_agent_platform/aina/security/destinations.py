import httpx

from tianzhou_agent_platform.core.errors import PlatformError


def _origin(value: str, *, configured: bool = False) -> tuple[str, str, int]:
    url = httpx.URL(value)
    if (
        url.scheme not in {"http", "https"}
        or not url.host
        or url.userinfo
        or url.fragment
        or (configured and (url.path != "/" or url.query))
    ):
        raise ValueError("Expected an HTTP(S) origin without credentials")
    return url.scheme, url.host, url.port or (443 if url.scheme == "https" else 80)


def require_approved_destination(url: str, allowed_origins: str) -> None:
    """Permit exact configured origins only; DNS and egress remain deployment controls."""
    try:
        allowed = {
            _origin(item.strip(), configured=True)
            for item in allowed_origins.split(",")
            if item.strip()
        }
        if _origin(url) in allowed:
            return
    except (ValueError, httpx.InvalidURL):
        pass
    raise PlatformError(
        "PERMISSION_DENIED",
        "Remote capability destination is not an approved HTTP(S) origin",
        status_code=403,
        source="capability",
        user_message="此远程能力的目标地址尚未获准。",
    )
