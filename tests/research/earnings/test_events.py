"""Event timing: which close is the first one after the release was public."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from alphadots_research.earnings.events import (
    candidates,
    drop_repeats,
    event_day,
    is_earnings_release,
    naive_day,
)
from alphadots_research.earnings.sources import Filing

# Mon 2024-01-08 .. Fri 2024-01-12, then Tue 2024-01-16 (Mon 15th is MLK Day), and July.
DAYS = [
    date(2024, 1, 8),
    date(2024, 1, 9),
    date(2024, 1, 10),
    date(2024, 1, 11),
    date(2024, 1, 12),
    date(2024, 1, 16),
    date(2024, 7, 1),
    date(2024, 7, 2),
]


def at(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


@pytest.mark.parametrize(
    ("accepted", "expected"),
    [
        (at(2024, 1, 9, 21, 30), date(2024, 1, 10)),  # 16:30 EST: after the close
        (at(2024, 1, 9, 20, 30), date(2024, 1, 9)),  # 15:30 EST: before the close
        (at(2024, 1, 9, 11, 0), date(2024, 1, 9)),  # 06:00 EST: pre-market
        (at(2024, 7, 1, 20, 30), date(2024, 7, 2)),  # 16:30 EDT: after the close
        (at(2024, 7, 1, 19, 59), date(2024, 7, 1)),  # 15:59 EDT
        (at(2024, 1, 12, 21, 5), date(2024, 1, 16)),  # Friday evening, over a holiday weekend
        (at(2024, 1, 14, 15, 0), date(2024, 1, 16)),  # Sunday
        (at(2024, 1, 15, 12, 0), date(2024, 1, 16)),  # the holiday itself, pre-market
    ],
)
def test_event_day_is_the_first_close_after_publication(accepted: datetime, expected: date) -> None:
    assert event_day(accepted, DAYS) == expected


def test_event_after_the_calendar_has_no_day() -> None:
    assert event_day(at(2024, 7, 2, 21, 0), DAYS) is None


def test_naive_day_ignores_the_time_of_day() -> None:
    # A 16:30 release filed on the 9th: a date-only dataset puts it on the 9th's close.
    assert naive_day(date(2024, 1, 9), DAYS) == date(2024, 1, 9)
    assert naive_day(date(2024, 1, 13), DAYS) == date(2024, 1, 16)


def filing(form: str, items: tuple[str, ...], accepted: datetime) -> Filing:
    return Filing(1, f"acc-{accepted.isoformat()}", form, accepted.date(), accepted, items)


def test_only_original_8k_item_202_counts() -> None:
    accepted = at(2024, 1, 9, 21, 30)
    assert is_earnings_release(filing("8-K", ("2.02", "9.01"), accepted))
    assert not is_earnings_release(filing("8-K/A", ("2.02",), accepted))
    assert not is_earnings_release(filing("8-K", ("5.02",), accepted))


def test_candidates_use_new_york_dates_for_the_window() -> None:
    # 03:00 UTC on Jan 1 is still Dec 31 in New York: outside a window starting Jan 1.
    early = filing("8-K", ("2.02",), at(2016, 1, 1, 3, 0))
    inside = filing("8-K", ("2.02",), at(2016, 1, 4, 21, 0))
    chosen = candidates([inside, early], date(2016, 1, 1), date(2016, 12, 31))
    assert chosen == [inside]


def test_repeats_within_five_trading_days_are_dropped() -> None:
    days = [date(2024, 1, d) for d in range(1, 31)]
    events = [date(2024, 1, 2), date(2024, 1, 5), date(2024, 1, 8), date(2024, 1, 20)]
    assert drop_repeats(events, days) == [True, False, True, True]
