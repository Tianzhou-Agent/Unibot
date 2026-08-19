"""Operations analytics projected from the durable OBS request stream."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from tianzhou_agent_platform.core.feedback import FeedbackRecord
from tianzhou_agent_platform.store.observability_store import ObservabilityStore

OPERATIONS_TIMEZONE = ZoneInfo("Asia/Shanghai")
METRIC_VERSION = "v1"
RANGE_DAYS = {"week": 7, "month": 30, "quarter": 90}
HISTORY_DAYS = 190


def operations_bounds(
    range_name: str,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime, date, int]:
    current = _aware(now or datetime.now(timezone.utc)).astimezone(OPERATIONS_TIMEZONE)
    days = RANGE_DAYS.get(range_name, RANGE_DAYS["week"])
    local_start = datetime.combine(
        current.date() - timedelta(days=days - 1),
        time.min,
        tzinfo=OPERATIONS_TIMEZONE,
    )
    return local_start.astimezone(timezone.utc), (current + timedelta(seconds=1)).astimezone(timezone.utc), current.date(), days


class OperationsAnalyticsService:
    def __init__(self, store: ObservabilityStore | None) -> None:
        self._store = store

    @property
    def enabled(self) -> bool:
        return self._store is not None

    async def overview(
        self,
        *,
        tenant_id: str | None,
        range_name: str,
        feedbacks: Sequence[FeedbackRecord] = (),
        now: datetime | None = None,
    ) -> dict[str, Any]:
        range_start, end, today, days = operations_bounds(range_name, now=now)
        history_start = datetime.combine(
            today - timedelta(days=HISTORY_DAYS),
            time.min,
            tzinfo=OPERATIONS_TIMEZONE,
        ).astimezone(timezone.utc)
        if self._store is None:
            return self._empty_overview(range_name, range_start, end, today, days)

        events, agent_events, first_uses, agent_first_uses = await asyncio.gather(
            self._store.list_operation_events(
                tenant_id=tenant_id,
                started_after=history_start,
                started_before=end,
            ),
            self._store.list_operation_agent_events(
                tenant_id=tenant_id,
                started_after=history_start,
                started_before=end,
            ),
            self._store.list_platform_first_uses(
                tenant_id=tenant_id,
                first_after=history_start,
                first_before=end,
            ),
            self._store.list_agent_first_uses(
                tenant_id=tenant_id,
                first_after=history_start,
                first_before=end,
            ),
        )
        range_events = [row for row in events if _aware(row["started_at"]) >= range_start]
        range_agent_events = [row for row in agent_events if _aware(row["started_at"]) >= range_start]
        activity = _activity_dates(events)
        platform_retention = {
            f"d{offset}": _retention(first_uses, activity, today=today, offset=offset, window_days=days)
            for offset in (1, 7, 30)
        }

        return {
            "range": range_name,
            "context": {
                "version": METRIC_VERSION,
                "timezone": str(OPERATIONS_TIMEZONE),
                "window": f"最近 {days} 个自然日",
                "from_at": range_start.isoformat(),
                "to_at": end.isoformat(),
                "as_of": end.isoformat(),
            },
            "availability": {
                "operations": True,
                "eligible_users": False,
                "department": False,
                "user_type": False,
            },
            "summary": _summary(events, range_events, today, platform_retention),
            "trend": _trend(range_events, start=range_start.astimezone(OPERATIONS_TIMEZONE).date(), end=today),
            "retention": platform_retention,
            "agents": _agent_rows(
                range_agent_events,
                agent_events,
                agent_first_uses,
                feedbacks,
                today=today,
                window_days=days,
            ),
            "cohorts": {
                "week": _cohorts(first_uses, activity, today=today, mode="week"),
                "month": _cohorts(first_uses, activity, today=today, mode="month"),
            },
        }

    @staticmethod
    def _empty_overview(
        range_name: str,
        range_start: datetime,
        end: datetime,
        today: date,
        days: int,
    ) -> dict[str, Any]:
        empty_retention = {name: {"rate": None, "cohort_users": 0} for name in ("d1", "d7", "d30")}
        return {
            "range": range_name,
            "context": {
                "version": METRIC_VERSION,
                "timezone": str(OPERATIONS_TIMEZONE),
                "window": f"最近 {days} 个自然日",
                "from_at": range_start.isoformat(),
                "to_at": end.isoformat(),
                "as_of": end.isoformat(),
            },
            "availability": {
                "operations": False,
                "eligible_users": False,
                "department": False,
                "user_type": False,
            },
            "summary": {
                "dau": 0,
                "wau": 0,
                "mau": 0,
                "dau_mau": None,
                "request_count": 0,
                "successful_requests": 0,
                "failed_requests": 0,
                "pending_requests": 0,
                "active_users": 0,
                "requests_per_active_user": None,
                "platform_penetration": None,
                "d7_retention": None,
            },
            "trend": _trend([], start=range_start.astimezone(OPERATIONS_TIMEZONE).date(), end=today),
            "retention": empty_retention,
            "agents": [],
            "cohorts": {"week": [], "month": []},
        }


def _summary(
    history_events: Sequence[dict[str, Any]],
    range_events: Sequence[dict[str, Any]],
    today: date,
    retention: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    users_by_date = _users_by_date(history_events)
    dau_users = set(users_by_date[today])
    wau_users = _users_in_window(users_by_date, today, 7)
    mau_users = _users_in_window(users_by_date, today, 30)
    range_users = {str(row["user_id"]) for row in range_events}
    request_count = len(range_events)
    return {
        "dau": len(dau_users),
        "wau": len(wau_users),
        "mau": len(mau_users),
        "dau_mau": _percentage(len(dau_users), len(mau_users)),
        "request_count": request_count,
        "successful_requests": sum(row.get("status") == "completed" for row in range_events),
        "failed_requests": sum(row.get("status") == "failed" for row in range_events),
        "pending_requests": sum(row.get("status") == "approval_required" for row in range_events),
        "active_users": len(range_users),
        "requests_per_active_user": round(request_count / len(range_users), 2) if range_users else None,
        "platform_penetration": None,
        "d7_retention": retention["d7"]["rate"],
    }


def _trend(events: Sequence[dict[str, Any]], *, start: date, end: date) -> list[dict[str, Any]]:
    requests: dict[date, int] = defaultdict(int)
    users: dict[date, set[str]] = defaultdict(set)
    for row in events:
        day = _local_date(row["started_at"])
        requests[day] += 1
        users[day].add(str(row["user_id"]))
    result: list[dict[str, Any]] = []
    day = start
    while day <= end:
        result.append({"date": day.isoformat(), "requests": requests[day], "active_users": len(users[day])})
        day += timedelta(days=1)
    return result


def _agent_rows(
    range_rows: Sequence[dict[str, Any]],
    history_rows: Sequence[dict[str, Any]],
    first_uses: Sequence[dict[str, Any]],
    feedbacks: Sequence[FeedbackRecord],
    *,
    today: date,
    window_days: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in range_rows:
        grouped[str(row["agent_id"])].append(row)
    history_activity: dict[tuple[str, str, str], set[date]] = defaultdict(set)
    for row in history_rows:
        history_activity[(str(row["tenant_id"]), str(row["user_id"]), str(row["agent_id"]))].add(
            _local_date(row["started_at"])
        )
    feedback_by_agent: dict[str, list[FeedbackRecord]] = defaultdict(list)
    for feedback in feedbacks:
        feedback_by_agent[feedback.agent_name].append(feedback)

    result: list[dict[str, Any]] = []
    for agent_id, rows in grouped.items():
        versions = [
            (row.get("started_at"), str(row.get("agent_version") or ""))
            for row in rows
            if row.get("agent_version")
        ]
        agent_feedbacks = feedback_by_agent.get(agent_id, [])
        positive_count = sum(item.rating == "up" for item in agent_feedbacks)
        first_for_agent = [row for row in first_uses if str(row["agent_id"]) == agent_id]
        retention = _retention(
            first_for_agent,
            history_activity,
            today=today,
            offset=7,
            window_days=window_days,
            scope_id=agent_id,
        )
        result.append(
            {
                "agent_id": agent_id,
                "agent_version": max(versions, default=(None, ""), key=lambda item: item[0] or datetime.min)[1],
                "eligible_users": None,
                "active_users": len({str(row["user_id"]) for row in rows}),
                "penetration": None,
                "requests": len({str(row["trace_id"]) for row in rows}),
                "positive_rate": _percentage(positive_count, len(agent_feedbacks)),
                "d7_retention": retention["rate"],
            }
        )
    return sorted(result, key=lambda row: (-row["requests"], row["agent_id"]))


def _retention(
    first_uses: Sequence[dict[str, Any]],
    activity: dict[Any, set[date]],
    *,
    today: date,
    offset: int,
    window_days: int,
    scope_id: str | None = None,
) -> dict[str, Any]:
    latest_first_date = today - timedelta(days=offset)
    earliest_first_date = latest_first_date - timedelta(days=window_days - 1)
    cohort = [
        row
        for row in first_uses
        if earliest_first_date <= _local_date(row["first_at"]) <= latest_first_date
    ]
    retained = 0
    for row in cohort:
        key: Any = (str(row["tenant_id"]), str(row["user_id"]))
        if scope_id is not None:
            key = (*key, scope_id)
        target = _local_date(row["first_at"]) + timedelta(days=offset)
        if target in activity.get(key, set()):
            retained += 1
    return {"rate": _percentage(retained, len(cohort)), "cohort_users": len(cohort)}


def _cohorts(
    first_uses: Sequence[dict[str, Any]],
    activity: dict[tuple[str, str], set[date]],
    *,
    today: date,
    mode: str,
) -> list[dict[str, Any]]:
    current = _period_start(today, mode)
    starts = [_add_periods(current, -index, mode) for index in reversed(range(4))]
    rows: list[dict[str, Any]] = []
    for cohort_start in starts:
        cohort_end = _add_periods(cohort_start, 1, mode)
        members = [
            row
            for row in first_uses
            if cohort_start <= _local_date(row["first_at"]) < cohort_end
        ]
        retention: list[float | None] = []
        for offset in range(5):
            period_start = _add_periods(cohort_start, offset, mode)
            period_end = _add_periods(period_start, 1, mode)
            if offset > 0 and period_end > today + timedelta(days=1):
                retention.append(None)
                continue
            retained = sum(
                any(period_start <= active_date < period_end for active_date in activity.get((str(row["tenant_id"]), str(row["user_id"])), set()))
                for row in members
            )
            retention.append(_percentage(retained, len(members)) if members else None)
        rows.append(
            {
                "cohort": cohort_start.isoformat(),
                "users": len(members),
                "retention": retention,
            }
        )
    return rows


def _activity_dates(events: Iterable[dict[str, Any]]) -> dict[tuple[str, str], set[date]]:
    activity: dict[tuple[str, str], set[date]] = defaultdict(set)
    for row in events:
        activity[(str(row["tenant_id"]), str(row["user_id"]))].add(_local_date(row["started_at"]))
    return activity


def _users_by_date(events: Iterable[dict[str, Any]]) -> dict[date, set[str]]:
    users: dict[date, set[str]] = defaultdict(set)
    for row in events:
        users[_local_date(row["started_at"])].add(str(row["user_id"]))
    return users


def _users_in_window(users_by_date: dict[date, set[str]], today: date, days: int) -> set[str]:
    result: set[str] = set()
    for offset in range(days):
        result.update(users_by_date[today - timedelta(days=offset)])
    return result


def _period_start(value: date, mode: str) -> date:
    if mode == "month":
        return value.replace(day=1)
    return value - timedelta(days=value.weekday())


def _add_periods(value: date, amount: int, mode: str) -> date:
    if mode == "week":
        return value + timedelta(weeks=amount)
    month_index = value.year * 12 + value.month - 1 + amount
    return date(month_index // 12, month_index % 12 + 1, 1)


def _local_date(value: Any) -> date:
    return _aware(value).astimezone(OPERATIONS_TIMEZONE).date()


def _aware(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime):
        raise TypeError(f"expected datetime, got {type(value)}")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _percentage(numerator: int, denominator: int) -> float | None:
    return round(numerator * 100 / denominator, 1) if denominator else None
