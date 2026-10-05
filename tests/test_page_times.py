"""The helpers every page writes a date, an instant, an age, and a percentage with.

KNOWN ANSWER: decided here before the helpers ran. An instant reaches the minute on UTC's clock
whatever zone it was recorded in, a stamp that cannot be read is shown as it came rather than
guessed at, and an age is whole days.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest

from obdi.page_times import (
    UTC_NOTE,
    age_text,
    date_text,
    date_with_age,
    instant_of,
    instant_text,
    month_text,
    percent_text,
    range_text,
)


class TestDatesAndRanges:
    def test_Dates_AreIso(self):
        assert date_text(date(2026, 6, 28)) == "2026-06-28"
        assert month_text(date(2026, 6, 28)) == "2026-06"

    def test_Range_IsFirstToLast(self):
        assert range_text(date(2026, 1, 10), date(2026, 6, 28)) == "2026-01-10 to 2026-06-28"


class TestInstants:
    def test_Instant_ForUtc_ReachesTheMinuteOnly(self):
        assert instant_text(datetime(2026, 10, 4, 15, 24, 59, 642171, tzinfo=UTC)) == (
            "2026-10-04 15:24"
        )

    def test_Instant_ForAnOffsetStamp_IsMovedToUtc(self):
        summer = timezone(timedelta(hours=1))

        assert instant_text(datetime(2026, 10, 4, 15, 24, tzinfo=summer)) == "2026-10-04 14:24"

    def test_Instant_ForAMomentWithNoZone_IsTakenAsUtc(self):
        naive = datetime.fromisoformat("2026-10-04T15:24:00")

        assert instant_text(naive) == "2026-10-04 15:24"

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("2026-10-04T14:25:07Z", "2026-10-04 14:25"),
            ("2026-10-04T20:35:15.651788+01:00", "2026-10-04 19:35"),
            ("not a stamp", "not a stamp"),
        ],
    )
    def test_InstantOf_ForARecordedStamp_IsTheOneFormatOrTheStampAsItCame(self, raw, expected):
        assert instant_of(raw) == expected

    def test_UtcNote_SaysTheZoneOnce(self):
        assert UTC_NOTE == "Times are UTC."


class TestADateWithItsAge:
    """KNOWN ANSWERS, worked by hand with today as 2026-10-05, before the helper was run."""

    TODAY = date(2026, 10, 5)

    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (date(2026, 10, 5), "2026-10-05"),
            (date(2026, 9, 22), "2026-09-22"),
            (date(2026, 9, 21), "2026-09-21 (2 weeks ago)"),
            (date(2026, 9, 14), "2026-09-14 (3 weeks ago)"),
            (date(2026, 8, 17), "2026-08-17 (7 weeks ago)"),
            (date(2026, 8, 6), "2026-08-06 (8 weeks ago)"),
            (date(2026, 8, 5), "2026-08-05 (2 months ago)"),
            (date(2025, 11, 6), "2025-11-06 (10 months ago)"),
            (date(2025, 11, 5), "2025-11-05 (11 months ago)"),
            (date(2025, 10, 6), "2025-10-06 (11 months ago)"),
            (date(2025, 10, 5), "2025-10-05 (over a year ago)"),
            (date(2024, 10, 6), "2024-10-06 (over a year ago)"),
            (date(2024, 10, 5), "2024-10-05 (over 2 years ago)"),
            (date(2016, 8, 4), "2016-08-04 (over 10 years ago)"),
        ],
    )
    def test_DateWithAge_AtEachThreshold_SaysWhatWasWorkedOutByHand(self, day, expected):
        assert date_with_age(day, self.TODAY) == expected

    def test_DateWithAge_ForADayInTheFuture_IsTheBareDate(self):
        assert date_with_age(date(2026, 12, 1), self.TODAY) == "2026-12-01"

    def test_DateWithAge_ForTheLastDayOfALongMonth_CountsCalendarMonthsNotThirtyDays(self):
        # 31 January to 28 February is a month short, and to 1 March is still short of one.
        assert date_with_age(date(2026, 1, 31), date(2026, 3, 30)) == "2026-01-31 (8 weeks ago)"
        assert date_with_age(date(2026, 1, 31), date(2026, 3, 31)) == (
            "2026-01-31 (2 months ago)"
        )

    def test_DateWithAge_AcrossAYearBoundary_CountsMonthsThroughTheNewYear(self):
        # Worked by hand: three calendar months on, less one because the 5th is before the 20th.
        assert date_with_age(date(2025, 12, 20), date(2026, 3, 5)) == "2025-12-20 (2 months ago)"

    def test_DateWithAge_WhenTheAgeIsWorthSaying_KeepsTheBareDateFirstSoItStillSortsAndReads(self):
        text = date_with_age(date(2026, 1, 1), self.TODAY)

        assert text.startswith("2026-01-01 (") and text.endswith(")")


class TestAgesAndShares:
    @pytest.mark.parametrize(
        ("days", "expected"),
        [(0, "(today)"), (1, "(yesterday)"), (98, "(98 days ago)"), (1204, "(1,204 days ago)")],
    )
    def test_Age_IsWholeDaysInBrackets(self, days, expected):
        assert age_text(days) == expected

    @pytest.mark.parametrize(
        ("share", "expected"), [(1.0, "100%"), (2 / 3, "66.7%"), (0.5, "50%"), (0.0, "0%")]
    )
    def test_Percent_UsesTheSignWithNoSpaceAndNoTrailingZero(self, share, expected):
        assert percent_text(share) == expected
