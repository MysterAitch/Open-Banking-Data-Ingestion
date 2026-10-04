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
