"""Expenses AINA tool server: a plain MCP server (Streamable HTTP) built with the official MCP Python SDK.

Run it with ``python main.py`` and register ``../package`` with Unibot. Unibot sends its context in the request
``_meta["unibot/context"]``. Expenses live in the AINA's private Host API ``storage``: the context token scopes every
read and write to the calling user, so this server keeps no user data of its own.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections import defaultdict
from datetime import date
from typing import Any

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel

CATEGORIES = {"food", "transport", "housing", "shopping", "health", "entertainment", "other"}

mcp = MCPServer("expenses", version="1.2.0")
# Tests route Host API calls to an in-process Unibot; None means real HTTP.
HOST_TRANSPORT: httpx.AsyncBaseTransport | None = None
# Background report jobs by task id. A real server would use a durable job queue.
JOBS: dict[str, asyncio.Task[None]] = {}

logger = logging.getLogger(__name__)


class Expense(BaseModel):
    id: str
    amount: float
    category: str
    note: str
    day: str
    currency: str


class ExpenseList(BaseModel):
    items: list[Expense]
    total: float


def _unibot(ctx: Context) -> dict[str, Any]:
    meta = ctx.request_context.meta or {}
    context = meta.get("unibot/context")
    if not isinstance(context, dict):
        raise ToolError("This server must be called by Unibot")
    return context


async def _host(context: dict[str, Any], method: str, path: str, **kwargs: Any) -> httpx.Response:
    async with httpx.AsyncClient(
        base_url=context["host"]["api_base"],
        headers={"Authorization": f"Bearer {context['token']}"},
        transport=HOST_TRANSPORT,
        timeout=10,
    ) as client:
        return await client.request(method, path, **kwargs)


async def _load(ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    context = _unibot(ctx)
    response = await _host(context, "GET", "/storage/kv/expenses")
    if response.status_code == 404:
        return [], context
    response.raise_for_status()
    return list(response.json()["value"]), context


async def _save(context: dict[str, Any], items: list[dict[str, Any]]) -> None:
    (await _host(context, "PUT", "/storage/kv/expenses", json={"value": items})).raise_for_status()


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
async def add_expense(amount: float, category: str, ctx: Context, note: str = "", day: str = "") -> Expense:
    """Record one expense for the user. Category is one of food, transport, housing, shopping, health,
    entertainment or other; day is YYYY-MM-DD and defaults to today."""
    if category not in CATEGORIES:
        raise ToolError(f"Unknown category {category!r}; use one of {', '.join(sorted(CATEGORIES))}")
    if amount <= 0:
        raise ToolError("The amount must be positive")
    items, context = await _load(ctx)
    # The invocation id is stable across host retries, so a retried call does not record the expense twice.
    invocation_id = context["invocation_id"]
    existing = next((item for item in items if item["invocation_id"] == invocation_id), None)
    if existing is None:
        existing = {
            "id": f"e_{max((int(item['id'][2:]) for item in items), default=0) + 1}",
            "invocation_id": invocation_id,
            "amount": amount,
            "category": category,
            "note": note,
            "day": day or date.today().isoformat(),
        }
        await _save(context, [*items, existing])
    return _expense(existing, context)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_expenses(ctx: Context, month: str = "") -> ExpenseList:
    """List the user's expenses, optionally only those of one month (YYYY-MM)."""
    items, context = await _load(ctx)
    selected = [_expense(item, context) for item in items if item["day"].startswith(month)]
    return ExpenseList(items=selected, total=round(sum(item.amount for item in selected), 2))


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def delete_expense(expense_id: str, ctx: Context) -> str:
    """Permanently delete one expense by its id."""
    items, context = await _load(ctx)
    remaining = [item for item in items if item["id"] != expense_id]
    if len(remaining) == len(items):
        raise ToolError(f"No expense has id {expense_id!r}")
    await _save(context, remaining)
    return f"Deleted expense {expense_id}"


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def monthly_report(month: str, ctx: Context) -> CallToolResult:
    """Build a spending report for one month (YYYY-MM): the total, totals per category and the largest expenses.
    The report is built in the background and posted to the conversation when it is ready."""
    context = _unibot(ctx)
    # One report per invocation: the invocation id is stable across host retries.
    task_id = "report-" + hashlib.sha256(context["invocation_id"].encode()).hexdigest()[:16]
    if task_id not in JOBS:
        JOBS[task_id] = asyncio.create_task(_build_report(dict(context), task_id, month))
    return CallToolResult(
        content=[TextContent(type="text", text=f"Building the spending report for {month}.")],
        _meta={"unibot/task": {"id": task_id, "title": f"Spending report · {month}"}},
    )


async def _build_report(context: dict[str, Any], task_id: str, month: str) -> None:
    """Report progress and the result on the Host API task callbacks (design §7.3)."""
    try:
        await _task_call(context, task_id, "progress", {"progress": 0, "total": 2, "message": "Reading expenses"})
        response = await _host_with_refresh(context, task_id, "GET", "/storage/kv/expenses")
        items = [] if response.status_code == 404 else list(response.raise_for_status().json()["value"])
        selected = [item for item in items if item["day"].startswith(month)]
        await _task_call(context, task_id, "progress", {"progress": 1, "total": 2, "message": "Summarizing"})
        by_category: dict[str, float] = defaultdict(float)
        for item in selected:
            by_category[item["category"]] += item["amount"]
        currency = str((context.get("config") or {}).get("currency") or "CNY")
        total = round(sum(by_category.values()), 2)
        largest = sorted(selected, key=lambda item: item["amount"], reverse=True)[:3]
        lines = [f"{month}: {total:.2f} {currency} across {len(selected)} expenses."]
        lines += [f"- {name}: {amount:.2f}" for name, amount in sorted(by_category.items(), key=lambda pair: -pair[1])]
        report = {
            "month": month,
            "currency": currency,
            "total": total,
            "by_category": {name: round(amount, 2) for name, amount in by_category.items()},
            "largest": [item["id"] for item in largest],
        }
        await _task_call(
            context,
            task_id,
            "complete",
            {"content": [{"type": "text", "text": "\n".join(lines)}], "structuredContent": report},
        )
    except Exception:
        logger.exception("The %s report failed", month)
        await _task_call(context, task_id, "fail", {"error": f"The {month} report could not be built"})


async def _task_call(context: dict[str, Any], task_id: str, action: str, body: dict[str, Any]) -> None:
    # The host registers the task when it reads the tool result, which can be just after the first callback.
    for _attempt in range(50):
        response = await _host_with_refresh(context, task_id, "POST", f"/tasks/{task_id}/{action}", json=body)
        if response.status_code != 404:
            response.raise_for_status()
            return
        await asyncio.sleep(0.1)
    response.raise_for_status()


async def _host_with_refresh(
    context: dict[str, Any], task_id: str, method: str, path: str, **kwargs: Any
) -> httpx.Response:
    """A Host API call that renews the context token (valid for 10 minutes) while the task is live."""
    response = await _host(context, method, path, **kwargs)
    if response.status_code != 401:
        return response
    refreshed = await _host(context, "POST", "/token/refresh", json={"task_id": task_id})
    refreshed.raise_for_status()
    context["token"] = refreshed.json()["token"]
    return await _host(context, method, path, **kwargs)


@mcp.tool()
async def sync_bank(ctx: Context) -> str:
    """Import new card transactions (called from the canvas only)."""
    _unibot(ctx)
    return "No new transactions"


def _expense(item: dict[str, Any], context: dict[str, Any]) -> Expense:
    currency = str((context.get("config") or {}).get("currency") or "CNY")
    return Expense(
        id=item["id"],
        amount=item["amount"],
        category=item["category"],
        note=item["note"],
        day=item["day"],
        currency=currency,
    )


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="127.0.0.1", port=8765, stateless_http=True)
