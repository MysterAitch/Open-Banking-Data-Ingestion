"""London's clock, from the rule and not from a time zone database.

The bank's feed states UTC and its app shows London time, so a row's time is shown
the way the app shows it. KNOWN ANSWERS, from the rule that summer time runs from
01:00 UTC on the last Sunday of March to 01:00 UTC on the last Sunday of October:

    2026: clocks go forward on Sunday 29 March and back on Sunday 25 October
    2027: forward on Sunday 28 March and back on Sunday 31 October
    2026-03-29 00:59 UTC is 00:59 in London, and 01:00 UTC is 02:00
    2026-10-25 00:59 UTC is 01:59 in London, and 01:00 UTC is 01:00
    2026-06-15 23:30 UTC is 00:30 on 2026-06-16 in London
    2026-12-15 23:30 UTC is 23:30 on 2026-12-15 in London
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from obdi.core.london_clock import london


def utc(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


def shown(text: str) -> str:
    return london(utc(text)).strftime("%Y-%m-%d %H:%M")


class TestLondonClock:
    @pytest.mark.parametrize(
        ("moment", "expected"),
        [
            ("2026-03-29T00:59", "2026-03-29 00:59"),
            ("2026-03-29T01:00", "2026-03-29 02:00"),
            ("2026-03-28T23:59", "2026-03-28 23:59"),
            ("2027-03-28T00:59", "2027-03-28 00:59"),
            ("2027-03-28T01:00", "2027-03-28 02:00"),
        ],
    )
    def test_ClocksGoingForward_RowsEitherSideOfTheChange_ShowTheBankAppsTime(
        self, moment, expected
    ):
        assert shown(moment) == expected

    @pytest.mark.parametrize(
        ("moment", "expected"),
        [
            ("2026-10-25T00:59", "2026-10-25 01:59"),
            ("2026-10-25T01:00", "2026-10-25 01:00"),
            ("2026-10-25T00:00", "2026-10-25 01:00"),
            ("2027-10-31T00:59", "2027-10-31 01:59"),
            ("2027-10-31T01:00", "2027-10-31 01:00"),
        ],
    )
    def test_ClocksGoingBack_RowsEitherSideOfTheChange_ShowTheBankAppsTime(
        self, moment, expected
    ):
        assert shown(moment) == expected

    def test_Summer_JustBeforeMidnightUtc_IsPastMidnightInLondonOnTheNextDate(self):
        assert shown("2026-06-15T23:30") == "2026-06-16 00:30"

    def test_Winter_JustBeforeMidnightUtc_IsTheSameMomentAndDateInLondon(self):
        assert shown("2026-12-15T23:30") == "2026-12-15 23:30"

    def test_Summer_JustAfterMidnightUtc_IsAnHourLaterOnTheSameDate(self):
        assert shown("2026-06-16T00:10") == "2026-06-16 01:10"

    def test_Moment_WhenGivenInAnotherOffset_IsReadAsTheSameInstant(self):
        from datetime import timedelta, timezone

        elsewhere = datetime(2026, 6, 16, 3, 30, tzinfo=timezone(timedelta(hours=4)))

        assert london(elsewhere).strftime("%H:%M") == "00:30"

    def test_Moment_WhenNoZoneIsGiven_IsRefusedRatherThanGuessed(self):
        with pytest.raises(ValueError, match="zone"):
            london(datetime(2026, 6, 16, 3, 30))  # noqa: DTZ001 - the absent zone is the case
