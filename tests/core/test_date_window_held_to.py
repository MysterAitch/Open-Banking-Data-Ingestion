"""A window is cut at the last day held, where a chart has one, and says so.

The balance chart holds days only up to the last known balance, which can be earlier than
today, so a window ending today would otherwise draw days nothing is known about. Worked out
by hand BEFORE the first run (held from 2019-01-01, today 2026-10-04):

    held to 2026-06-30
        last 90 days               2026-07-07 to 2026-10-04 asked: wholly after what is held,
                                   so empty, and the sentence names 2026-06-30
        last 12 months             2025-10-05 to 2026-10-04 asked: shown 2025-10-05 to
                                   2026-06-30, cut after what is held
        2026 to date (calendar)    2026-01-01 to 2026-06-30, cut after what is held
        between 2022-01-01 and
          2022-12-31               not cut: 2022-01-01 to 2022-12-31
    held to 2026-10-04 (the same day as today)
        last 12 months             2025-10-05 to 2026-10-04, not cut
    held to nothing stated         as before: only today cuts
"""

from __future__ import annotations

from datetime import date

from obdi.core.date_window import Period, Unit, between, length, named, resolve

D = date
HELD_FROM = D(2019, 1, 1)
TODAY = D(2026, 10, 4)
HELD_TO = D(2026, 6, 30)


class TestCutAtTheLastDayHeld:
    def test_WindowWhollyAfterTheLastDayHeld_IsEmptyAndNamesThatDay(self):
        window = resolve(
            length(90, Unit.DAYS), today=TODAY, held_from=HELD_FROM, held_to=HELD_TO
        )

        assert window.empty and window.first is None
        assert "2026-06-30" in window.empty_reason
        assert "2026-07-07" in window.empty_reason

    def test_WindowReachingPastTheLastDayHeld_IsCutThereAndSaysSo(self):
        window = resolve(
            length(12, Unit.MONTHS), today=TODAY, held_from=HELD_FROM, held_to=HELD_TO
        )

        assert (window.first, window.last) == (D(2025, 10, 5), D(2026, 6, 30))
        assert window.ended_after_held is True
        assert window.ended_in_future is False

    def test_CalendarYearToDate_IsCutAtTheLastDayHeld(self):
        window = resolve(
            named(Period.YEAR_TO_DATE), today=TODAY, held_from=HELD_FROM, held_to=HELD_TO
        )

        assert (window.first, window.last) == (D(2026, 1, 1), D(2026, 6, 30))
        assert window.ended_after_held is True

    def test_WindowEndingBeforeTheLastDayHeld_IsNotCut(self):
        window = resolve(
            between(D(2022, 1, 1), D(2022, 12, 31)),
            today=TODAY,
            held_from=HELD_FROM,
            held_to=HELD_TO,
        )

        assert (window.first, window.last) == (D(2022, 1, 1), D(2022, 12, 31))
        assert window.ended_after_held is False

    def test_LastDayHeldTheSameAsToday_CutsNothing(self):
        window = resolve(
            length(12, Unit.MONTHS), today=TODAY, held_from=HELD_FROM, held_to=TODAY
        )

        assert (window.first, window.last) == (D(2025, 10, 5), TODAY)
        assert window.ended_after_held is False

    def test_NoLastDayHeldGiven_OnlyTodayCuts(self):
        window = resolve(length(12, Unit.MONTHS), today=TODAY, held_from=HELD_FROM)

        assert window.last == TODAY
        assert window.ended_after_held is False

    def test_PeriodRunningIntoTheFuture_SaysBothCuts(self):
        window = resolve(
            named(Period.THIS_YEAR), today=TODAY, held_from=HELD_FROM, held_to=HELD_TO
        )

        assert window.last == HELD_TO
        assert window.ended_in_future is True
        assert window.ended_after_held is True
