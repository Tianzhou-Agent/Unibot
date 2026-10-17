"""Expenses AINA tool server: a plain MCP server (Streamable HTTP) built with the official MCP Python SDK.

Run it with ``python main.py`` and register ``../package`` with Unibot. Unibot sends its context in the request
``_meta["unibot/context"]``; this example keys expenses by the pairwise user id found there. Until the Host API
ships (protocol phase P2), expenses are kept in process memory instead of ``storage``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel

CATEGORIES = {"food", "transport", "housing", "shopping", "health", "entertainment", "other"}

mcp = MCPServer("expenses", version="1.2.0")
_expenses: dict[str, list[dict[str, Any]]] = {}


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


def _user_items(ctx: Context) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    context = _unibot(ctx)
    return _expenses.setdefault(context["user"]["id"], []), context


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
async def add_expense(amount: float, category: str, ctx: Context, note: str = "", day: str = "") -> Expense:
    """Record one expense for the user. Category is one of food, transport, housing, shopping, health,
    entertainment or other; day is YYYY-MM-DD and defaults to today."""
    if category not in CATEGORIES:
        raise ToolError(f"Unknown category {category!r}; use one of {', '.join(sorted(CATEGORIES))}")
    if amount <= 0:
        raise ToolError("The amount must be positive")
    items, context = _user_items(ctx)
    # The invocation id is stable across host retries, so a retried call does not record the expense twice.
    invocation_id = context["invocation_id"]
    existing = next((item for item in items if item["invocation_id"] == invocation_id), None)
    if existing is None:
        existing = {
            "id": f"e_{len(items) + 1}",
            "invocation_id": invocation_id,
            "amount": amount,
            "category": category,
            "note": note,
            "day": day or date.today().isoformat(),
        }
        items.append(existing)
    return _expense(existing, context)


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def list_expenses(ctx: Context, month: str = "") -> ExpenseList:
    """List the user's expenses, optionally only those of one month (YYYY-MM)."""
    items, context = _user_items(ctx)
    selected = [_expense(item, context) for item in items if item["day"].startswith(month)]
    return ExpenseList(items=selected, total=round(sum(item.amount for item in selected), 2))


@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
async def delete_expense(expense_id: str, ctx: Context) -> str:
    """Permanently delete one expense by its id."""
    items, _context = _user_items(ctx)
    for index, item in enumerate(items):
        if item["id"] == expense_id:
            del items[index]
            return f"Deleted expense {expense_id}"
    raise ToolError(f"No expense has id {expense_id!r}")


@mcp.tool()
async def sync_bank(ctx: Context) -> str:
    """Import new card transactions (called from the canvas only)."""
    _user_items(ctx)
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
