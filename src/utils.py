"""Shared helpers used across the project."""
from __future__ import annotations

import dataclasses
from datetime import date, timedelta

from .logging import setup_log, log  # noqa: F401 — re-exported

PERIODS = ["biweekly", "monthly", "last-month"]


def resolve_period(
    period: str,
) -> tuple[str, str]:
    """Return (start, end) as YYYY-MM-DD strings for the requested period."""
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
    raise ValueError(f"Unknown period: {period}")


def file_stem(period: str) -> str:
    """Build a filename stem like 'self-report-biweekly-2026-06-16'."""
    return f"self-report-{period}-{date.today()}"


def to_json(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    if isinstance(obj, list):
        return [to_json(i) for i in obj]
    return obj
