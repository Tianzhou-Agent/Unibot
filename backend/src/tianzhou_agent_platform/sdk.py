from __future__ import annotations

from typing import Any, cast

import httpx


class UnibotClient:
    """Small async Python SDK for the MVP HTTP surface."""

    def __init__(self, base_url: str, *, timeout: float = 60.0) -> None:
        self._client = httpx.AsyncClient(base_url=base_url.rstrip("/"), timeout=timeout)

    async def __aenter__(self) -> "UnibotClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self,
        message: str,
        *,
        conversation_id: str | None = None,
        user_id: str = "anonymous",
        tenant_id: str = "default",
        capability: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/chat",
            json={
                "message": message,
                "conversation_id": conversation_id,
                "user_id": user_id,
                "tenant_id": tenant_id,
                "capability": capability,
            },
        )
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def create_conversation(
        self,
        *,
        title: str = "New conversation",
        user_id: str = "anonymous",
        tenant_id: str = "default",
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/conversations",
            json={"title": title, "user_id": user_id, "tenant_id": tenant_id},
        )
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def get_trace(self, trace_id: str) -> dict[str, Any]:
        response = await self._client.get(f"/traces/{trace_id}")
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def register_tool(self, definition: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post("/tools", json=definition)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def register_aina(self, manifest: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post("/ainas", json=manifest)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())

    async def install_aina(self, aina_id: str, installation: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(f"/ainas/{aina_id}/install", json=installation)
        response.raise_for_status()
        return cast(dict[str, Any], response.json())
