"""Shared helpers used across the project."""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta

from .logging import setup_log, log  # noqa: F401; re-exported

PERIODS = ["biweekly", "monthly", "last-month", "yearly", "historic", "custom"]


def resolve_period(
    period: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str | None, str | None]:
    """Return (start, end) as YYYY-MM-DD strings for the requested period.

    `historic` returns (None, None): no date bounds (all time).
    `custom` requires start_date and end_date.
    """
    today = date.today()
    if period == "biweekly":
        return str(today - timedelta(weeks=2)), str(today)
    if period == "monthly":
        return str(today.replace(day=1)), str(today)
    if period == "last-month":
        first_of_this_month = today.replace(day=1)
        last_of_last_month = first_of_this_month - timedelta(days=1)
        first_of_last_month = last_of_last_month.replace(day=1)
        return str(first_of_last_month), str(last_of_last_month)
    if period == "yearly":
        return str(today.replace(month=1, day=1)), str(today)
    if period == "historic":
        return None, None
    if period == "custom":
        if not start_date or not end_date:
            raise ValueError("custom period requires --start-date and --end-date (YYYY-MM-DD)")
        try:
            s = date.fromisoformat(start_date)
            e = date.fromisoformat(end_date)
        except ValueError as err:
            raise ValueError(f"Invalid date format: {err}")
        if s > e:
            raise ValueError(f"--start-date ({start_date}) must be before --end-date ({end_date})")
        return start_date, end_date
    raise ValueError(f"Unknown period: {period}")


def is_multi_month(start: str | None, end: str | None) -> bool:
    """Whether the period spans enough time to warrant monthly trend charts."""
    if start is None:
        return True  # historic: all time
    try:
        s = date.fromisoformat(start)
        e = date.fromisoformat(end) if end else date.today()
    except ValueError:
        return False
    return (e - s).days > 45


def file_stem(period: str) -> str:
    """Build a filename stem like 'self-report-biweekly-2026-06-16'."""
    return f"self-report-{period}-{date.today()}"


def to_json(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, list):
        return [to_json(i) for i in obj]
    return obj
