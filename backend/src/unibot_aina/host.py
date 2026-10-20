"""Typed client for the Superbot Host API (design §8), authorized by a call's context token."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, cast
from urllib.parse import quote, unquote

import httpx

_in_process: tuple[httpx.AsyncBaseTransport, str] | None = None


def set_default_transport(transport: httpx.AsyncBaseTransport | None, *, api_base: str = "") -> None:
    """Send Host API calls through ``transport`` to ``api_base`` instead of the context's public URL.

    Superbot uses it to serve its bundled servers in process; ``None`` restores ordinary HTTP.
    """
    global _in_process
    _in_process = None if transport is None else (transport, api_base)


class HostApiError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class PassedFile:
    """A file the user attached and this tool call was passed by its ``file_ref``."""

    name: str
    content_type: str
    data: bytes


class HostApi:
    def __init__(self, api_base: str, token: str, *, timeout: float = 30.0) -> None:
        self.api_base = api_base.rstrip("/")
        self.token = token
        self.timeout = timeout

    # --- storage ------------------------------------------------------------------------------------------------

    async def storage_get(self, key: str, default: Any = None) -> Any:
        response = await self._request("GET", f"/storage/kv/{_path(key)}", missing_ok=True)
        return default if response is None else response["value"]

    async def storage_put(self, key: str, value: Any) -> None:
        await self._request("PUT", f"/storage/kv/{_path(key)}", json={"value": value})

    async def storage_delete(self, key: str) -> None:
        await self._request("DELETE", f"/storage/kv/{_path(key)}", missing_ok=True)

    async def storage_list(self, prefix: str = "", limit: int = 100) -> list[dict[str, Any]]:
        result = await self._request("GET", "/storage/kv", params={"prefix": prefix, "limit": limit})
        return list(result["items"])

    async def put_file(self, path: str, data: bytes, content_type: str = "application/octet-stream") -> dict[str, Any]:
        """Create or replace a private file of this installation (at most 10 MiB; 100 MiB and 1000 files in all)."""
        return cast(
            dict[str, Any],
            await self._request(
                "PUT", f"/storage/files/{_path(path)}", content=data, headers={"Content-Type": content_type}
            ),
        )

    async def get_file(self, path: str) -> bytes | None:
        """A private file's bytes, or ``None`` when there is no such file."""
        response = await self._request("GET", f"/storage/files/{_path(path)}", missing_ok=True, raw=True)
        return None if response is None else bytes(response.content)

    async def passed_file(self, file_ref: str) -> PassedFile | None:
        """A file the user attached, when this call's arguments carried its ``file_ref``; else ``None``."""
        response = await self._request("GET", f"/files/{_path(file_ref)}", missing_ok=True, raw=True)
        if response is None:
            return None
        return PassedFile(
            name=_file_name(response.headers.get("content-disposition", "")) or file_ref,
            content_type=response.headers.get("content-type", "application/octet-stream").split(";")[0],
            data=bytes(response.content),
        )

    async def delete_file(self, path: str) -> None:
        await self._request("DELETE", f"/storage/files/{_path(path)}", missing_ok=True)

    async def list_files(self, prefix: str = "", limit: int = 100) -> list[dict[str, Any]]:
        result = await self._request("GET", "/storage/files", params={"prefix": prefix, "limit": limit})
        return list(result["items"])

    # --- documents ----------------------------------------------------------------------------------------------

    async def documents(self, query: str | None = None) -> list[dict[str, Any]]:
        params = {"query": query} if query else None
        return list((await self._request("GET", "/documents", params=params))["items"])

    async def document(self, name: str) -> dict[str, Any] | None:
        return cast("dict[str, Any] | None", await self._request("GET", f"/documents/{_path(name)}", missing_ok=True))

    async def document_outline(self, name: str) -> dict[str, Any]:
        return cast(dict[str, Any], await self._request("GET", f"/documents/{_path(name)}/outline"))

    async def put_document(self, name: str, content: str) -> dict[str, Any]:
        return cast(dict[str, Any], await self._request("PUT", f"/documents/{_path(name)}", json={"content": content}))

    async def delete_document(self, name: str) -> None:
        await self._request("DELETE", f"/documents/{_path(name)}")

    # --- memory -------------------------------------------------------------------------------------------------

    async def search_memory(self, query: str = "", limit: int = 8) -> list[dict[str, Any]]:
        return list((await self._request("GET", "/memory/search", params={"query": query, "limit": limit}))["items"])

    async def remember(self, content: str, category: str = "fact") -> dict[str, Any]:
        return cast(
            dict[str, Any], await self._request("POST", "/memory", json={"content": content, "category": category})
        )

    # --- notify -------------------------------------------------------------------------------------------------

    async def notify(self, title: str, body: str = "", *, opens: str = "app") -> dict[str, Any]:
        """Put a plain-text entry in the user's inbox; it opens your app, or with ``opens="conversation"`` the
        conversation of this call. At most 30 per hour."""
        return cast(
            dict[str, Any],
            await self._request("POST", "/notifications", json={"title": title, "body": body, "opens": opens}),
        )

    # --- llm ----------------------------------------------------------------------------------------------------

    async def chat(self, messages: list[dict[str, str]], *, max_tokens: int | None = None) -> str:
        body: dict[str, Any] = {"messages": messages}
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        return str((await self._request("POST", "/llm/chat", json=body))["content"])

    # --- tasks (design §7.3) ------------------------------------------------------------------------------------

    async def task_progress(
        self, task_id: str, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        await self._request(
            "POST", f"/tasks/{_path(task_id)}/progress", json={"progress": progress, "total": total, "message": message}
        )

    async def task_state(self, task_id: str) -> dict[str, Any]:
        """The task as Superbot sees it; stop working when its ``status`` is ``cancelled``."""
        return cast(dict[str, Any], await self._request("GET", f"/tasks/{_path(task_id)}"))

    async def task_complete(self, task_id: str, text: str, structured: dict[str, Any] | None = None) -> None:
        body: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
        if structured is not None:
            body["structuredContent"] = structured
        await self._request("POST", f"/tasks/{_path(task_id)}/complete", json=body)

    async def task_fail(self, task_id: str, error: str) -> None:
        await self._request("POST", f"/tasks/{_path(task_id)}/fail", json={"error": error})

    async def _request(
        self, method: str, path: str, *, missing_ok: bool = False, raw: bool = False, **kwargs: Any
    ) -> Any:
        transport, api_base = _in_process or (None, self.api_base)
        async with httpx.AsyncClient(
            base_url=api_base,
            headers={"Authorization": f"Bearer {self.token}"},
            transport=transport,
            timeout=self.timeout,
        ) as client:
            response = await client.request(method, path, **kwargs)
        if missing_ok and response.status_code == 404:
            return None
        if response.status_code >= 400:
            error = (response.json() if response.content else {}).get("error") or {}
            raise HostApiError(
                response.status_code, str(error.get("code") or "ERROR"), str(error.get("message") or response.text)
            )
        if raw:
            return response
        return response.json() if response.content else None


def _file_name(disposition: str) -> str:
    encoded = re.search(r"filename\*=UTF-8''([^;]+)", disposition)
    if encoded:
        return unquote(encoded.group(1).strip())
    plain = re.search(r'filename="([^"]*)"', disposition)
    return plain.group(1) if plain else ""


def _path(value: str) -> str:
    return quote(value, safe="/")
