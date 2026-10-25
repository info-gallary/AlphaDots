"""Earnings-release events and their timing.

The point-in-time rule: a release accepted by EDGAR before 16:00 New York time on a
trading day moves that day's close; anything later moves the next trading day's close.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from datetime import date, datetime, time

from alphadots_research.earnings.sources import NEW_YORK, Filing

MARKET_CLOSE = time(16, 0)
EARNINGS_ITEM = "2.02"
DUPLICATE_WINDOW_DAYS = 5


def is_earnings_release(filing: Filing) -> bool:
    """An original 8-K reporting Item 2.02 (Results of Operations and Financial Condition)."""
    return filing.form == "8-K" and EARNINGS_ITEM in filing.items


def new_york(moment: datetime) -> datetime:
    return moment.astimezone(NEW_YORK)


def candidates(filings: list[Filing], start: date, end: date) -> list[Filing]:
    """Earnings releases accepted between ``start`` and ``end`` (New York dates), in order."""
    chosen = [
        filing
        for filing in filings
        if is_earnings_release(filing) and start <= new_york(filing.accepted_at).date() <= end
    ]
    return sorted(chosen, key=lambda filing: (filing.accepted_at, filing.accession))


def event_day(accepted_at: datetime, trading_days: Sequence[date]) -> date | None:
    """Day 0: the first trading day whose close comes after the release was public."""
    local = new_york(accepted_at)
    day = local.date()
    index = bisect_left(trading_days, day)
    if index < len(trading_days) and trading_days[index] == day and local.time() < MARKET_CLOSE:
        return day
    index = bisect_right(trading_days, day)
    return trading_days[index] if index < len(trading_days) else None


def naive_day(filing_date: date, trading_days: Sequence[date]) -> date | None:
    """What a date-only dataset suggests: the filing date (or the next trading day).

    For a release accepted after the close this is one day too early; trading at its
    close means trading before the news was public.
    """
    index = bisect_left(trading_days, filing_date)
    return trading_days[index] if index < len(trading_days) else None


def drop_repeats(days: list[date], trading_days: Sequence[date]) -> list[bool]:
    """Keep flags for one firm's event days in time order: an event within
    ``DUPLICATE_WINDOW_DAYS`` trading days after the previous kept one is a repeat."""
    position = {day: i for i, day in enumerate(trading_days)}
    keep: list[bool] = []
    last: int | None = None
    for day in days:
        here = position[day]
        if last is not None and here - last <= DUPLICATE_WINDOW_DAYS:
            keep.append(False)
            continue
        keep.append(True)
        last = here
    return keep
