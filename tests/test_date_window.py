"""A window of dates, worked out from a small description of it.

The owner asked to see a chart over "3/6/12/18/24/whatever" days, weeks, months, or
years, not necessarily the last ones, and aligned to calendar or tax-year
boundaries. The answers below were written out by hand BEFORE the first run
(inclusive both ends, so a window of twelve months is exactly a year of days):

    today 2026-10-04 (a Sunday)
        this month            2026-10-01 to 2026-10-31, shown to 2026-10-04
        last month            2026-09-01 to 2026-09-30
        this quarter          2026-10-01 to 2026-12-31, shown to 2026-10-04
        last quarter          2026-07-01 to 2026-09-30
        this calendar year    2026-01-01 to 2026-12-31, shown to 2026-10-04
        last calendar year    2025-01-01 to 2025-12-31
        calendar year to date 2026-01-01 to 2026-10-04
        this tax year         2026-04-06 to 2027-04-05, shown to 2026-10-04
        last tax year         2025-04-06 to 2026-04-05
        tax year to date      2026-04-06 to 2026-10-04
    today 2026-04-05, the last day of a tax year
        this tax year         2025-04-06 to 2026-04-05, nothing in the future
        last tax year         2024-04-06 to 2025-04-05
        tax year to date      2025-04-06 to 2026-04-05
    today 2026-04-06, the first day of the next
        this tax year         2026-04-06 to 2027-04-05, shown to 2026-04-06 (one day)
        last tax year         2025-04-06 to 2026-04-05
        tax year to date      2026-04-06 to 2026-04-06
    today 2026-01-01
        this month            2026-01-01 to 2026-01-31, shown to 2026-01-01
        last month            2025-12-01 to 2025-12-31
        last quarter          2025-10-01 to 2025-12-31
        last calendar year    2025-01-01 to 2025-12-31
        this tax year         2025-04-06 to 2026-04-05, shown to 2026-01-01
        last tax year         2024-04-06 to 2025-04-05
    today 2024-02-29 (a leap day)
        this month            2024-02-01 to 2024-02-29, nothing in the future
        last month            2024-01-01 to 2024-01-31
        last quarter          2023-10-01 to 2023-12-31
        this tax year         2023-04-06 to 2024-04-05, shown to 2024-02-29
        last tax year         2022-04-06 to 2023-04-05
    lengths, today 2026-10-04
        12 months ending today  2025-10-05 to 2026-10-04
        3 months                2026-07-05 to 2026-10-04
        18 months               2025-04-05 to 2026-10-04
        24 months, 2 years      2024-10-05 to 2026-10-04
        90 days                 2026-07-07 to 2026-10-04
        4 weeks                 2026-09-07 to 2026-10-04
        1 day                   2026-10-04 to 2026-10-04
    anniversaries, which clamp where the day does not exist
        12 months ending 2024-02-29   2023-03-01 to 2024-02-29 (366 days)
        12 months ending 2025-02-28   2024-02-29 to 2025-02-28 (366 days)
        1 month ending 2026-03-31     2026-03-01 to 2026-03-31
        1 month starting 2026-01-31   2026-01-31 to 2026-02-27 (the anniversary is 02-28)
        1 month starting 2026-02-28   2026-02-28 to 2026-03-27
        12 months starting 2024-02-29 2024-02-29 to 2025-02-27
        1 month starting 2026-01-01   2026-01-01 to 2026-01-31
        2 weeks starting 2026-10-01   2026-10-01 to 2026-10-14

The point of each resolution is a day, a Sunday, or a month-end. A window of
2026-10-01 to 2026-10-04 holds 4 days; the Sundays of 2026-09-28 to 2026-10-11 are
2026-10-04 and 2026-10-11.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.date_window import (
    DAILY_UP_TO_DAYS,
    TAX_YEAR_STARTS,
    WEEKLY_UP_TO_DAYS,
    Anchor,
    Period,
    Resolution,
    Unit,
    WindowRefused,
    between,
    everything,
    length,
    named,
    resolution_for,
    resolve,
    sample_days,
)

D = date
HELD = D(2019, 1, 5)


def span_of(spec, today, held=HELD):
    window = resolve(spec, today=today, held_from=held)
    return window.first, window.last


NAMED_TABLE = [
    # today, period, first, last, shown-to-today
    (D(2026, 10, 4), Period.THIS_MONTH, D(2026, 10, 1), D(2026, 10, 4), True),
    (D(2026, 10, 4), Period.LAST_MONTH, D(2026, 9, 1), D(2026, 9, 30), False),
    (D(2026, 10, 4), Period.THIS_QUARTER, D(2026, 10, 1), D(2026, 10, 4), True),
    (D(2026, 10, 4), Period.LAST_QUARTER, D(2026, 7, 1), D(2026, 9, 30), False),
    (D(2026, 10, 4), Period.THIS_YEAR, D(2026, 1, 1), D(2026, 10, 4), True),
    (D(2026, 10, 4), Period.LAST_YEAR, D(2025, 1, 1), D(2025, 12, 31), False),
    (D(2026, 10, 4), Period.YEAR_TO_DATE, D(2026, 1, 1), D(2026, 10, 4), False),
    (D(2026, 10, 4), Period.THIS_TAX_YEAR, D(2026, 4, 6), D(2026, 10, 4), True),
    (D(2026, 10, 4), Period.LAST_TAX_YEAR, D(2025, 4, 6), D(2026, 4, 5), False),
    (D(2026, 10, 4), Period.TAX_YEAR_TO_DATE, D(2026, 4, 6), D(2026, 10, 4), False),
    (D(2026, 4, 5), Period.THIS_TAX_YEAR, D(2025, 4, 6), D(2026, 4, 5), False),
    (D(2026, 4, 5), Period.LAST_TAX_YEAR, D(2024, 4, 6), D(2025, 4, 5), False),
    (D(2026, 4, 5), Period.TAX_YEAR_TO_DATE, D(2025, 4, 6), D(2026, 4, 5), False),
    (D(2026, 4, 6), Period.THIS_TAX_YEAR, D(2026, 4, 6), D(2026, 4, 6), True),
    (D(2026, 4, 6), Period.LAST_TAX_YEAR, D(2025, 4, 6), D(2026, 4, 5), False),
    (D(2026, 4, 6), Period.TAX_YEAR_TO_DATE, D(2026, 4, 6), D(2026, 4, 6), False),
    (D(2026, 1, 1), Period.THIS_MONTH, D(2026, 1, 1), D(2026, 1, 1), True),
    (D(2026, 1, 1), Period.LAST_MONTH, D(2025, 12, 1), D(2025, 12, 31), False),
    (D(2026, 1, 1), Period.LAST_QUARTER, D(2025, 10, 1), D(2025, 12, 31), False),
    (D(2026, 1, 1), Period.LAST_YEAR, D(2025, 1, 1), D(2025, 12, 31), False),
    (D(2026, 1, 1), Period.THIS_TAX_YEAR, D(2025, 4, 6), D(2026, 1, 1), True),
    (D(2026, 1, 1), Period.LAST_TAX_YEAR, D(2024, 4, 6), D(2025, 4, 5), False),
    (D(2024, 2, 29), Period.THIS_MONTH, D(2024, 2, 1), D(2024, 2, 29), False),
    (D(2024, 2, 29), Period.LAST_MONTH, D(2024, 1, 1), D(2024, 1, 31), False),
    (D(2024, 2, 29), Period.LAST_QUARTER, D(2023, 10, 1), D(2023, 12, 31), False),
    (D(2024, 2, 29), Period.THIS_TAX_YEAR, D(2023, 4, 6), D(2024, 2, 29), True),
    (D(2024, 2, 29), Period.LAST_TAX_YEAR, D(2022, 4, 6), D(2023, 4, 5), False),
]


class TestNamedPeriods:
    @pytest.mark.parametrize(("today", "period", "first", "last", "clipped"), NAMED_TABLE)
    def test_NamedPeriod_OnTheDay_RunsFromItsFirstDayToItsLast(
        self, today, period, first, last, clipped
    ):
        window = resolve(named(period), today=today, held_from=D(2015, 1, 1))

        assert (window.first, window.last) == (first, last)
        assert window.ended_in_future is clipped

    def test_TaxYear_IsTheUksSixthOfAprilUntilTheFifth(self):
        assert TAX_YEAR_STARTS == (4, 6)

    def test_PeriodsThatEndInTheFuture_SayWhereTheyWouldHaveEnded(self):
        window = resolve(named(Period.THIS_TAX_YEAR), today=D(2026, 10, 4), held_from=HELD)

        assert window.asked_last == D(2027, 4, 5)
        assert window.last == D(2026, 10, 4)


class TestLengths:
    @pytest.mark.parametrize(
        ("count", "unit", "first"),
        [
            (12, Unit.MONTHS, D(2025, 10, 5)),
            (3, Unit.MONTHS, D(2026, 7, 5)),
            (18, Unit.MONTHS, D(2025, 4, 5)),
            (24, Unit.MONTHS, D(2024, 10, 5)),
            (2, Unit.YEARS, D(2024, 10, 5)),
            (90, Unit.DAYS, D(2026, 7, 7)),
            (4, Unit.WEEKS, D(2026, 9, 7)),
            (1, Unit.DAYS, D(2026, 10, 4)),
        ],
    )
    def test_LengthEndingToday_StartsTheDayAfterTheSameDayThatManyUnitsAgo(
        self, count, unit, first
    ):
        assert span_of(length(count, unit), D(2026, 10, 4)) == (first, D(2026, 10, 4))

    def test_TwelveMonthsEndingOnALeapDay_IsExactlyAYearOfDays(self):
        first, last = span_of(
            length(12, Unit.MONTHS, Anchor.ENDING_ON, D(2024, 2, 29)), D(2026, 10, 4)
        )

        assert (first, last) == (D(2023, 3, 1), D(2024, 2, 29))
        assert (last - first).days + 1 == 366

    def test_TwelveMonthsEndingOnTheDayAfterALeapYearsFebruary_HoldsTheLeapDay(self):
        first, last = span_of(
            length(12, Unit.MONTHS, Anchor.ENDING_ON, D(2025, 2, 28)), D(2026, 10, 4)
        )

        assert (first, last) == (D(2024, 2, 29), D(2025, 2, 28))

    def test_OneMonthEndingOnThe31st_IsTheCalendarMonth(self):
        spec = length(1, Unit.MONTHS, Anchor.ENDING_ON, D(2026, 3, 31))

        assert span_of(spec, D(2026, 10, 4)) == (D(2026, 3, 1), D(2026, 3, 31))

    def test_OneMonthStartingOnThe31st_EndsTheDayBeforeFebruarysAnniversary(self):
        assert span_of(
            length(1, Unit.MONTHS, Anchor.STARTING_ON, D(2026, 1, 31)), D(2026, 10, 4)
        ) == (D(2026, 1, 31), D(2026, 2, 27))

    def test_ConsecutiveMonthsStartingOnThe31st_TileWithoutGapOrOverlap(self):
        _, first_end = span_of(
            length(1, Unit.MONTHS, Anchor.STARTING_ON, D(2026, 1, 31)), D(2026, 10, 4)
        )
        next_first, _ = span_of(
            length(1, Unit.MONTHS, Anchor.STARTING_ON, D(2026, 2, 28)), D(2026, 10, 4)
        )

        assert next_first.toordinal() - first_end.toordinal() == 1

    def test_TwelveMonthsStartingOnALeapDay_EndsBeforeTheClampedAnniversary(self):
        assert span_of(
            length(12, Unit.MONTHS, Anchor.STARTING_ON, D(2024, 2, 29)), D(2026, 10, 4)
        ) == (D(2024, 2, 29), D(2025, 2, 27))

    def test_LengthStartingOnAStatedDay_RunsForwardInclusive(self):
        weeks = length(2, Unit.WEEKS, Anchor.STARTING_ON, D(2026, 10, 1))
        month = length(1, Unit.MONTHS, Anchor.STARTING_ON, D(2026, 1, 1))

        assert span_of(weeks, D(2026, 10, 30)) == (D(2026, 10, 1), D(2026, 10, 14))
        assert span_of(month, D(2026, 10, 4)) == (D(2026, 1, 1), D(2026, 1, 31))


class TestWhereTheWindowRunsOutOfWhatIsHeld:
    def test_WindowEndingInTheFuture_IsClippedToTodayAndSaysSo(self):
        window = resolve(
            length(3, Unit.MONTHS, Anchor.ENDING_ON, D(2026, 12, 31)),
            today=D(2026, 10, 4), held_from=HELD,
        )

        assert (window.first, window.last) == (D(2026, 10, 1), D(2026, 10, 4))
        assert window.ended_in_future is True
        assert window.asked_last == D(2026, 12, 31)

    def test_WindowEndingToday_IsNotClipped(self):
        window = resolve(length(3, Unit.MONTHS), today=D(2026, 10, 4), held_from=HELD)

        assert window.ended_in_future is False

    def test_WindowStartingBeforeAnythingIsHeld_IsClippedToWhatIsHeldAndSaysSo(self):
        window = resolve(length(12, Unit.MONTHS), today=D(2026, 10, 4), held_from=D(2026, 3, 1))

        assert window.first == D(2026, 3, 1)
        assert window.asked_first == D(2025, 10, 5)
        assert window.began_before_held is True

    def test_WindowStartingAfterWhatIsHeldBegan_IsNotClipped(self):
        window = resolve(length(3, Unit.MONTHS), today=D(2026, 10, 4), held_from=D(2026, 3, 1))

        assert window.began_before_held is False

    def test_WindowWhollyBeforeAnythingIsHeld_IsEmptyAndSaysWhy(self):
        window = resolve(
            between(D(2024, 1, 1), D(2024, 12, 31)), today=D(2026, 10, 4), held_from=D(2026, 3, 1)
        )

        assert window.empty is True
        assert window.first is None and window.last is None
        assert "2026-03-01" in window.empty_reason
        assert "before" in window.empty_reason

    def test_WindowWhollyInTheFuture_IsEmptyAndSaysWhy(self):
        window = resolve(
            between(D(2027, 1, 1), D(2027, 3, 31)), today=D(2026, 10, 4), held_from=HELD
        )

        assert window.empty is True
        assert "after today" in window.empty_reason

    def test_NothingHeldAtAll_IsEmptyAndSaysSo(self):
        window = resolve(length(3, Unit.MONTHS), today=D(2026, 10, 4), held_from=None)

        assert window.empty is True
        assert "nothing" in window.empty_reason.lower()

    def test_AbsurdLength_IsClippedToWhatIsHeldWithoutFailing(self):
        window = resolve(length(10_000, Unit.YEARS), today=D(2026, 10, 4), held_from=D(2019, 1, 5))

        assert (window.first, window.last) == (D(2019, 1, 5), D(2026, 10, 4))
        assert window.began_before_held is True

    def test_AbsurdLengthInDays_IsClippedToWhatIsHeldWithoutFailing(self):
        window = resolve(length(10**12, Unit.DAYS), today=D(2026, 10, 4), held_from=D(2019, 1, 5))

        assert window.first == D(2019, 1, 5)

    def test_AbsurdLengthStartingOnADay_IsClippedToTodayWithoutFailing(self):
        window = resolve(
            length(10_000, Unit.YEARS, Anchor.STARTING_ON, D(2020, 1, 1)),
            today=D(2026, 10, 4), held_from=HELD,
        )

        assert window.last == D(2026, 10, 4)
        assert window.ended_in_future is True


class TestRefusals:
    @pytest.mark.parametrize("count", [0, -1, -12])
    def test_Length_OfZeroOrLess_IsRefusedInASentence(self, count):
        with pytest.raises(WindowRefused) as refused:
            resolve(length(count, Unit.MONTHS), today=D(2026, 10, 4), held_from=HELD)

        assert str(refused.value) == "A length must be a whole number of at least 1."

    def test_FromAfterTo_IsRefusedInASentenceNamingBothDays(self):
        with pytest.raises(WindowRefused) as refused:
            resolve(between(D(2022, 2, 1), D(2022, 1, 31)), today=D(2026, 10, 4), held_from=HELD)

        assert str(refused.value) == (
            "The window starts on 2022-02-01, which is after it ends on 2022-01-31."
        )

    def test_FromEqualToTo_IsOneDayAndNotRefused(self):
        assert span_of(between(D(2022, 1, 31), D(2022, 1, 31)), D(2026, 10, 4)) == (
            D(2022, 1, 31), D(2022, 1, 31),
        )

    def test_LengthAnchoredOnADayWithoutOne_IsAProgrammingFaultNotAUserRefusal(self):
        with pytest.raises(ValueError, match="needs a day") as fault:
            length(3, Unit.MONTHS, Anchor.ENDING_ON)

        assert not isinstance(fault.value, WindowRefused)


class TestEverythingAndTheBetweenWindow:
    def test_Everything_IsFromTheFirstDayHeldToToday(self):
        window = resolve(everything(), today=D(2026, 10, 4), held_from=D(2023, 11, 5))

        assert (window.first, window.last) == (D(2023, 11, 5), D(2026, 10, 4))
        assert not window.ended_in_future and not window.began_before_held

    def test_Between_RunsFromTheFirstDayToTheSecondInclusive(self):
        assert span_of(between(D(2021, 11, 1), D(2022, 1, 31)), D(2026, 10, 4)) == (
            D(2021, 11, 1), D(2022, 1, 31),
        )

    def test_Today_IsNeverReadFromTheClock(self):
        a = resolve(length(1, Unit.DAYS), today=D(1999, 12, 31), held_from=D(1990, 1, 1))
        b = resolve(length(1, Unit.DAYS), today=D(2030, 1, 1), held_from=D(1990, 1, 1))

        assert a.last == D(1999, 12, 31) and b.last == D(2030, 1, 1)


class TestResolutionFollowsTheWindowsLength:
    @pytest.mark.parametrize(
        ("days", "expected"),
        [
            (1, Resolution.DAY),
            (DAILY_UP_TO_DAYS, Resolution.DAY),
            (DAILY_UP_TO_DAYS + 1, Resolution.WEEK),
            (WEEKLY_UP_TO_DAYS, Resolution.WEEK),
            (WEEKLY_UP_TO_DAYS + 1, Resolution.MONTH),
            (3000, Resolution.MONTH),
        ],
    )
    def test_Resolution_EitherSideOfEachThreshold_ChangesAtTheBoundary(self, days, expected):
        first = D(2020, 1, 1)
        assert resolution_for(first, D.fromordinal(first.toordinal() + days - 1)) is expected

    def test_Thresholds_KeepEveryMarkAtLeastThreeUnitsFromItsNeighbour(self):
        width = 376
        assert width / (DAILY_UP_TO_DAYS - 1) >= 3
        assert width / (WEEKLY_UP_TO_DAYS / 7 + 1) >= 3


class TestSampleDays:
    def test_Daily_IsEveryDayInclusive(self):
        days = sample_days(D(2026, 9, 30), D(2026, 10, 2), Resolution.DAY)

        assert days == [D(2026, 9, 30), D(2026, 10, 1), D(2026, 10, 2)]

    def test_Weekly_IsTheFirstDayEachSundayAndTheLastDay(self):
        days = sample_days(D(2026, 9, 30), D(2026, 10, 14), Resolution.WEEK)

        assert days == [D(2026, 9, 30), D(2026, 10, 4), D(2026, 10, 11), D(2026, 10, 14)]

    def test_Weekly_WhereTheWindowStartsAndEndsOnSundays_RepeatsNothing(self):
        days = sample_days(D(2026, 10, 4), D(2026, 10, 18), Resolution.WEEK)

        assert days == [D(2026, 10, 4), D(2026, 10, 11), D(2026, 10, 18)]

    def test_Weekly_OfOneDay_IsThatDay(self):
        assert sample_days(D(2026, 10, 6), D(2026, 10, 6), Resolution.WEEK) == [D(2026, 10, 6)]

    def test_Monthly_IsEachMonthEndAndTheLastDay(self):
        days = sample_days(D(2024, 1, 15), D(2024, 4, 10), Resolution.MONTH)

        assert days == [D(2024, 1, 31), D(2024, 2, 29), D(2024, 3, 31), D(2024, 4, 10)]

    def test_Monthly_WhereTheWindowEndsOnAMonthEnd_RepeatsNothing(self):
        days = sample_days(D(2024, 1, 15), D(2024, 3, 31), Resolution.MONTH)

        assert days == [D(2024, 1, 31), D(2024, 2, 29), D(2024, 3, 31)]

    def test_Last_IsAlwaysTheWindowsLastDay(self):
        for resolution in Resolution:
            days = sample_days(D(2024, 1, 3), D(2026, 10, 4), resolution)
            assert days[-1] == D(2026, 10, 4)


class TestDescribing:
    def test_Description_NamesTheChoiceInWords(self):
        assert everything().describe(today=D(2026, 10, 4)) == "Everything held"
        assert length(12, Unit.MONTHS).describe(today=D(2026, 10, 4)) == (
            "12 months ending 2026-10-04"
        )
        assert length(1, Unit.WEEKS, Anchor.STARTING_ON, D(2026, 1, 5)).describe(
            today=D(2026, 10, 4)
        ) == "1 week starting 2026-01-05"
        assert named(Period.LAST_TAX_YEAR).describe(today=D(2026, 10, 4)) == "Last tax year"
        assert between(D(2021, 11, 1), D(2022, 1, 31)).describe(today=D(2026, 10, 4)) == (
            "From 2021-11-01 to 2022-01-31"
        )
