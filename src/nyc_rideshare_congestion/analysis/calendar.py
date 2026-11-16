"""Days, windows and holiday lists from the plan's parameter block."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any


def _day(text: str) -> date:
    return date.fromisoformat(text)


def day_range(first: date, last: date) -> list[date]:
    """Every day from first to last, both ends included."""
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def parse_dates(items: list[str]) -> set[date]:
    """Expand the plan's date list. "a/b" means every day from a to b."""
    days: set[date] = set()
    for item in items:
        if "/" in item:
            first, last = item.split("/")
            days.update(day_range(_day(first), _day(last)))
        else:
            days.add(_day(item))
    return days


def window(p: dict[str, Any], name: str) -> tuple[date, date]:
    first, last = p["windows"][name]
    return _day(first), _day(last)


def window_days(p: dict[str, Any], name: str, excluded: set[date] | None = None) -> list[date]:
    """Days of one window, minus any excluded days."""
    first, last = window(p, name)
    skip = excluded or set()
    return [d for d in day_range(first, last) if d not in skip]


def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def week_start(d: date) -> date:
    """The Monday of the calendar week (Monday to Sunday) holding d."""
    return d - timedelta(days=d.weekday())
