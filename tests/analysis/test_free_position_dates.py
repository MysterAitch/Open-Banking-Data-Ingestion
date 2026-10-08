"""When a commitment's window next falls due, with the answers decided before the first run.

A window is read from its cadence, usual day, and first day; the answers are worked out on a
calendar by hand.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.analysis.free_position import next_due, owes_kind
from obdi.ingest.commitment_records import Window


def window(cadence: str, usual_day: int, from_day: date, usual_month: int = 0) -> Window:
    return Window(1, 1, from_day, None, 1000, "GBP", cadence, usual_day, usual_month, 4, "invented")


@pytest.mark.parametrize(
    ("cadence", "usual_day", "from_day", "today", "month", "expected"),
    [
        ("monthly", 25, date(2026, 1, 25), date(2026, 10, 8), 0, date(2026, 10, 25)),
        ("monthly", 5, date(2026, 1, 5), date(2026, 10, 8), 0, date(2026, 11, 5)),
        ("monthly", 8, date(2026, 1, 8), date(2026, 10, 8), 0, date(2026, 10, 8)),
        # A 31st falls on the last day of a shorter month.
        ("monthly", 31, date(2026, 1, 31), date(2026, 2, 10), 0, date(2026, 2, 28)),
        # The year turns.
        ("monthly", 3, date(2025, 3, 3), date(2026, 12, 20), 0, date(2027, 1, 3)),
        # Quarterly from January: January, April, July, October.
        ("quarterly", 15, date(2026, 1, 15), date(2026, 10, 20), 0, date(2027, 1, 15)),
        ("quarterly", 15, date(2026, 1, 15), date(2026, 2, 1), 0, date(2026, 4, 15)),
        # Yearly in its usual month.
        ("yearly", 14, date(2024, 3, 14), date(2026, 10, 8), 3, date(2027, 3, 14)),
        ("yearly", 14, date(2024, 3, 14), date(2026, 3, 1), 3, date(2026, 3, 14)),
        # Fortnightly keeps the phase of the first day: 2026-01-02 + 14n is ... 09-25, 10-09, 10-23
        # (counted off a calendar; the first draft of this answer said 10-16, which is off-phase).
        ("fortnightly", 4, date(2026, 1, 2), date(2026, 10, 8), 0, date(2026, 10, 9)),
        ("fortnightly", 4, date(2026, 1, 2), date(2026, 10, 10), 0, date(2026, 10, 23)),
        # Weekly from a Friday.
        ("weekly", 4, date(2026, 1, 2), date(2026, 10, 8), 0, date(2026, 10, 9)),
        # A window that has not begun is due on its first day.
        ("weekly", 4, date(2026, 12, 4), date(2026, 10, 8), 0, date(2026, 12, 4)),
    ],
)
def test_NextDue_ForEachCadence_IsTheFirstDayOnOrAfterToday(
    cadence, usual_day, from_day, today, month, expected
):
    assert next_due(window(cadence, usual_day, from_day, month), today) == expected


@pytest.mark.parametrize(
    ("cadence", "usual_day"), [("sometimes", 5), ("monthly", 0), ("monthly", 32)]
)
def test_NextDue_WhenTheCadenceOrDayCannotPlaceIt_IsNone(cadence, usual_day):
    assert next_due(window(cadence, usual_day, date(2026, 1, 1)), date(2026, 10, 8)) is None


@pytest.mark.parametrize(
    ("kind", "owes"),
    [("credit card", True), ("Credit-Card", True), ("loan", True), ("mortgage", True),
     ("current", False), ("", False), ("balance-only", False)],
)
def test_OwesKind_ForADeclaredKind_SaysWhetherTheBalanceIsOwed(kind, owes):
    assert owes_kind(kind) is owes
