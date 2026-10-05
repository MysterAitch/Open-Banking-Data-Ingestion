"""Named periods aligned to the week: this week, last week, and the last four whole weeks.

A week is Monday to Sunday, the way the Position chart's weekly points already close a week
(on a Sunday). The answers were worked out by hand BEFORE the first run, from the calendar
(2026-10-05 is a Monday, so 2026-10-04 is the Sunday before it; 2026-01-01 is a Thursday;
2024-02-29 is a Thursday):

    today 2026-10-05, a Monday
        this week         2026-10-05 to 2026-10-11, shown to 2026-10-05 (one day, cut)
        last week         2026-09-28 to 2026-10-04
        last 4 weeks      2026-09-07 to 2026-10-04 (28 days, the Monday four weeks back)
    today 2026-10-04, a Sunday
        this week         2026-09-28 to 2026-10-04, not cut: today is the week's last day
        last week         2026-09-21 to 2026-09-27
        last 4 weeks      2026-08-31 to 2026-09-27
    today 2026-01-01, a Thursday that is also New Year's Day
        this week         2025-12-29 to 2026-01-04, shown to 2026-01-01
        last week         2025-12-22 to 2025-12-28
        last 4 weeks      2025-12-01 to 2025-12-28
    today 2024-02-29, a leap day, a Thursday
        this week         2024-02-26 to 2024-03-03, shown to 2024-02-29
        last week         2024-02-19 to 2024-02-25
        last 4 weeks      2024-01-29 to 2024-02-25 (28 days across the leap day's month)

"Last 4 weeks" is four WHOLE weeks ending last Sunday, which is what "Last week" and
"Last month" already mean (whole, and not including today), where four weeks ending today
is the length "4 weeks ending today". Both stay available, so neither is hidden.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from obdi.date_window import Period, named, resolve

D = date
HELD = D(2015, 1, 1)

WEEK_TABLE = [
    # today, period, first, last, cut at today
    (D(2026, 10, 5), Period.THIS_WEEK, D(2026, 10, 5), D(2026, 10, 5), True),
    (D(2026, 10, 5), Period.LAST_WEEK, D(2026, 9, 28), D(2026, 10, 4), False),
    (D(2026, 10, 5), Period.LAST_4_WEEKS, D(2026, 9, 7), D(2026, 10, 4), False),
    (D(2026, 10, 4), Period.THIS_WEEK, D(2026, 9, 28), D(2026, 10, 4), False),
    (D(2026, 10, 4), Period.LAST_WEEK, D(2026, 9, 21), D(2026, 9, 27), False),
    (D(2026, 10, 4), Period.LAST_4_WEEKS, D(2026, 8, 31), D(2026, 9, 27), False),
    (D(2026, 1, 1), Period.THIS_WEEK, D(2025, 12, 29), D(2026, 1, 1), True),
    (D(2026, 1, 1), Period.LAST_WEEK, D(2025, 12, 22), D(2025, 12, 28), False),
    (D(2026, 1, 1), Period.LAST_4_WEEKS, D(2025, 12, 1), D(2025, 12, 28), False),
    (D(2024, 2, 29), Period.THIS_WEEK, D(2024, 2, 26), D(2024, 2, 29), True),
    (D(2024, 2, 29), Period.LAST_WEEK, D(2024, 2, 19), D(2024, 2, 25), False),
    (D(2024, 2, 29), Period.LAST_4_WEEKS, D(2024, 1, 29), D(2024, 2, 25), False),
]


class TestWeekAlignedPeriods:
    @pytest.mark.parametrize(("today", "period", "first", "last", "cut"), WEEK_TABLE)
    def test_WeekPeriod_OnTheDay_RunsFromItsFirstDayToItsLast(
        self, today, period, first, last, cut
    ):
        window = resolve(named(period), today=today, held_from=HELD)

        assert (window.first, window.last) == (first, last)
        assert window.ended_in_future is cut

    def test_ThisWeekCutAtToday_SaysWhereItWouldHaveEnded(self):
        window = resolve(named(Period.THIS_WEEK), today=D(2026, 10, 5), held_from=HELD)

        assert window.asked_last == D(2026, 10, 11)

    @pytest.mark.parametrize("offset", range(14))
    @pytest.mark.parametrize("period", [Period.LAST_WEEK, Period.LAST_4_WEEKS])
    def test_WholeWeekPeriods_OnAnyDay_BeginOnAMondayAndEndOnASundayBeforeToday(
        self, period, offset
    ):
        today = D(2026, 10, 5) + timedelta(days=offset)

        window = resolve(named(period), today=today, held_from=HELD)

        assert window.first is not None and window.last is not None
        assert window.first.weekday() == 0
        assert window.last.weekday() == 6
        assert window.last < today
        assert (today - window.last).days <= 7

    @pytest.mark.parametrize("offset", range(14))
    def test_ThisWeek_OnAnyDay_BeginsOnTheMondayOnOrBeforeToday(self, offset):
        today = D(2026, 10, 5) + timedelta(days=offset)

        window = resolve(named(Period.THIS_WEEK), today=today, held_from=HELD)

        assert window.first is not None
        assert window.first.weekday() == 0
        assert 0 <= (today - window.first).days <= 6

    def test_LastFourWeeks_IsTwentyEightDaysAndLastWeekIsTheNewestOfThem(self):
        today = D(2026, 10, 12)

        four = resolve(named(Period.LAST_4_WEEKS), today=today, held_from=HELD)
        one = resolve(named(Period.LAST_WEEK), today=today, held_from=HELD)

        assert four.days == 28
        assert four.last == one.last

    def test_LastWeek_OfAnAccountHeldOnlyFromWednesday_IsCutAtWhatIsHeldAndSaysSo(self):
        window = resolve(named(Period.LAST_WEEK), today=D(2026, 10, 5), held_from=D(2026, 9, 30))

        assert (window.first, window.last) == (D(2026, 9, 30), D(2026, 10, 4))
        assert window.began_before_held is True
