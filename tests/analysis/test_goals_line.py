"""The straight line a goal is measured against: whole calendar months, in pence.

KNOWN ANSWERS, decided before the first run. A month is complete on the same day of the next
month, or on the last day of a month too short to have it; a date before the start is no months;
the line is never shorter than one month, so a goal due within a month is still measured.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.analysis.goals import Line, line_of, months_between


class TestWholeMonths:
    @pytest.mark.parametrize(
        ("first", "last", "months"),
        [
            (date(2026, 5, 15), date(2026, 9, 15), 4),
            (date(2026, 5, 15), date(2026, 9, 14), 3),
            (date(2026, 5, 15), date(2026, 5, 15), 0),
            (date(2026, 9, 15), date(2026, 5, 15), 0),
            (date(2026, 5, 15), date(2027, 5, 15), 12),
            (date(2026, 11, 30), date(2027, 2, 28), 3),
            (date(2026, 1, 31), date(2026, 2, 28), 1),
            (date(2026, 1, 31), date(2026, 2, 27), 0),
            (date(2024, 2, 29), date(2025, 2, 28), 12),
            (date(2026, 12, 31), date(2027, 1, 30), 0),
        ],
        ids=[
            "four-months",
            "a-day-short",
            "same-day",
            "backwards",
            "a-year",
            "month-end-to-month-end",
            "thirty-first-to-short-february",
            "short-february-a-day-early",
            "leap-day-to-plain-year",
            "thirty-first-to-thirtieth-of-a-longer-next-month",
        ],
    )
    def test_MonthsBetween_ForTheDaysGiven_IsTheWholeMonthsCompleted(self, first, last, months):
        assert months_between(first, last) == months


class TestTheLine:
    def test_Line_WhenTheDateIsWithinAMonth_IsOneMonthLongAndNotYetStarted(self):
        assert line_of(date(2026, 9, 1), date(2026, 9, 20), date(2026, 9, 10)) == Line(1, 0)

    def test_Line_WhenTheDateHasPassed_StopsAtItsEndAndIsOver(self):
        line = line_of(date(2026, 2, 1), date(2026, 8, 1), date(2026, 9, 15))

        assert line == Line(6, 6)
        assert line.over

    def test_Line_WhenFourMonthsOfTwelveHavePassed_IsAThirdOfTheWay(self):
        line = line_of(date(2026, 5, 15), date(2027, 5, 15), date(2026, 9, 15))

        assert line == Line(12, 4)
        assert not line.over
        # In pence, whole: 1,200.00 over twelve months is 100.00 a month, 400.00 after four.
        assert 120000 * line.elapsed // line.total == 40000
