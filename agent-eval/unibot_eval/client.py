"""Thin async client for the Unibot REST surface used by the evaluations."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

import httpx

from unibot_eval.telemetry import inject_trace_headers


class BackendError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, body: Any = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.body = body


@dataclass
class ChatResult:
    response: dict[str, Any]
    latency_ms: float
    ttft_ms: float | None


class UnibotEvalClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 180.0,
        headers: dict[str, str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_s,
            headers=headers or {},
            transport=transport,
        )

    async def __aenter__(self) -> "UnibotEvalClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    # ---- agent turns -------------------------------------------------------------------------------------

    async def chat(self, payload: dict[str, Any], *, stream: bool) -> ChatResult:
        return await (self._chat_stream(payload) if stream else self._chat_blocking(payload))

    async def _chat_blocking(self, payload: dict[str, Any]) -> ChatResult:
        started = time.perf_counter()
        response = await self._client.post("/chat", json=payload, headers=inject_trace_headers({}))
        latency_ms = (time.perf_counter() - started) * 1000
        return ChatResult(response=_json_or_raise(response), latency_ms=latency_ms, ttft_ms=None)

    async def _chat_stream(self, payload: dict[str, Any]) -> ChatResult:
        started = time.perf_counter()
        ttft_ms: float | None = None
        final: dict[str, Any] | None = None
        async with self._client.stream(
            "POST", "/chat/stream", json=payload, headers=inject_trace_headers({})
        ) as response:
            if response.status_code >= 400:
                body = await response.aread()
                raise BackendError(
                    f"POST /chat/stream -> {response.status_code}", status_code=response.status_code, body=body
                )
            async for event in _sse_events(response):
                kind = event.get("type")
                if kind == "message.delta" and ttft_ms is None:
                    ttft_ms = (time.perf_counter() - started) * 1000
                elif kind == "message.completed":
                    final = event["response"]
                elif kind == "error" and "error" in event:
                    # Terminal run failure. Capability errors ({"type": "error", "source": "capability"}) carry no
                    # "error" object: they are returned to the model and the run continues.
                    raise BackendError(f"agent stream error: {event.get('error')}", body=event)
        latency_ms = (time.perf_counter() - started) * 1000
        if final is None:
            raise BackendError("stream ended without message.completed")
        return ChatResult(response=final, latency_ms=latency_ms, ttft_ms=ttft_ms)

    async def confirm_approval(self, approval_id: str, *, user_id: str, tenant_id: str) -> ChatResult:
        started = time.perf_counter()
        response = await self._client.post(
            f"/approvals/{approval_id}/confirm",
            json={"user_id": user_id, "tenant_id": tenant_id},
            headers=inject_trace_headers({}),
        )
        return ChatResult(_json_or_raise(response), (time.perf_counter() - started) * 1000, None)

    async def deny_approval(self, approval_id: str, *, user_id: str, tenant_id: str) -> dict[str, Any]:
        response = await self._client.post(
            f"/approvals/{approval_id}/deny",
            json={"user_id": user_id, "tenant_id": tenant_id},
            headers=inject_trace_headers({}),
        )
        return _json_or_raise(response)

    # ---- observability -----------------------------------------------------------------------------------

    async def get_trace(self, trace_id: str, *, wait_s: float = 10.0) -> dict[str, Any]:
        """Fetch the backend trace, waiting briefly while background span processing finishes."""
        deadline = time.monotonic() + wait_s
        while True:
            response = await self._client.get(f"/traces/{trace_id}")
            trace = _json_or_raise(response)
            if trace.get("status") != "running" or time.monotonic() >= deadline:
                return trace
            await asyncio.sleep(0.5)

    async def llm_calls_for_trace(self, trace_id: str, *, user_id: str, tenant_id: str) -> list[dict[str, Any]]:
        """Return the exact LLM requests of one run (what the model actually saw), oldest first.

        Returns an empty list when the deployment does not expose ``/llm-calls`` to this caller.
        """
        response = await self._client.get(
            "/llm-calls", params={"user_id": user_id, "tenant_id": tenant_id, "limit": 500}
        )
        if response.status_code in {401, 403, 404}:
            return []
        calls = [call for call in _json_or_raise(response) if call.get("trace_id") == trace_id]
        return sorted(calls, key=lambda call: str(call.get("created_at") or ""))

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """A plain API call for setup and polling steps: (status code, JSON body or text)."""
        response = await self._client.request(
            method, path, json=body, params=params or None, headers=inject_trace_headers({})
        )
        try:
            return response.status_code, response.json()
        except ValueError:
            return response.status_code, response.text

    async def delete_conversation(self, conversation_id: str) -> None:
        response = await self._client.delete(f"/conversations/{conversation_id}")
        if response.status_code not in {200, 204, 404}:
            raise BackendError(f"DELETE conversation -> {response.status_code}", status_code=response.status_code)


def _json_or_raise(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        raise BackendError(
            f"{response.request.method} {response.request.url.path} -> {response.status_code}: {response.text[:500]}",
            status_code=response.status_code,
            body=response.text,
        )
    return response.json()


async def _sse_events(response: httpx.Response) -> Any:
    data_lines: list[str] = []
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line == "" and data_lines:
            yield json.loads("\n".join(data_lines))
            data_lines = []
    if data_lines:
        yield json.loads("\n".join(data_lines))
