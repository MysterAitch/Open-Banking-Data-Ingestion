"""Window arithmetic behind the extend buttons."""

from __future__ import annotations

from datetime import date, timedelta

from obdi.cli import extend_bounds


class TestExtendWindowBounds:
    """The extend anchor must never ask the provider for the future.

    Proven live: on an account holding nothing yet the anchor fell back to
    today, the one-day overlap pushed `until` to tomorrow, and the provider
    refused with invalid_date_range - "`to` cannot be in the future".
    """

    def test_Extend_WithHeldHistory_WalksBackFromEarliestWithOneDayOverlap(self):
        since, until = extend_bounds(date(2024, 8, 2), 730, today=date(2026, 8, 2))

        assert until == date(2024, 8, 3)
        assert since == date(2024, 8, 2) - timedelta(days=730)

    def test_Extend_FirstPressOnAnEmptyAccount_EndsTodayNotTomorrow(self):
        since, until = extend_bounds(None, 90, today=date(2026, 8, 2))

        assert until == date(2026, 8, 2)
        assert since == date(2026, 8, 2) - timedelta(days=90)

    def test_Extend_WhenTheEarliestHeldIsToday_StillDoesNotReachTomorrow(self):
        _since, until = extend_bounds(date(2026, 8, 2), 7, today=date(2026, 8, 2))

        assert until == date(2026, 8, 2)
