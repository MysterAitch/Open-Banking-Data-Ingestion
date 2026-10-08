"""The days a window falls due across a span, and the state a due day is given.

Known answers, decided before the first run. `due_days` is asked from the day after each answer
with `free_position.next_due`, so the calendar places a payment where Position does.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from obdi.analysis.free_position import due_days, next_due
from obdi.analysis.recurring import PULLED, Series
from obdi.analysis.this_month import DUE, NOT_TAKEN, OVERDUE, PAID, _state
from obdi.ingest.commitment_records import Window


def window(
    cadence: str, usual_day: int, *, from_day: date, to_day: date | None = None, tolerance: int = 4
) -> Window:
    return Window(1, 1, from_day, to_day, 1000, "GBP", cadence, usual_day, 0, tolerance, "invented")


def series(**changes: object) -> Series:
    base = Series(
        account="a", off_account=0, other_account="", shape="x", label="X", currency="GBP",
        direction="out", cadence="monthly", usual_day=5, usual_month=0, weekday=None,
        usual_minor=1000, min_minor=1000, max_minor=1000, latest_minor=1000, drift_percent=0.0,
        steady=True, count=6, kind=PULLED, basis="by type: Direct Debit", periods=6, explained=0,
        missed=0, first_seen=date(2026, 3, 5), last_seen=date(2026, 8, 5),
        next_expected=date(2026, 9, 5), is_transfer=False, is_income=False, stopped=False,
        changed=False, seen_days=(date(2026, 7, 5), date(2026, 8, 5)),
    )
    return replace(base, **changes)


def september(w: Window) -> list[date]:
    return due_days(w, date(2026, 9, 1), date(2026, 9, 30))


class TestDueDays:
    def test_DueDays_Monthly_FallsOnceInTheMonthOnTheUsualDay(self):
        assert september(window("monthly", 20, from_day=date(2026, 1, 1))) == [date(2026, 9, 20)]

    def test_DueDays_MonthlyOnThe31st_ClampsToTheMonthsLastDay(self):
        found = due_days(
            window("monthly", 31, from_day=date(2026, 1, 1)), date(2026, 2, 1), date(2026, 2, 28)
        )

        assert found == [date(2026, 2, 28)]

    def test_DueDays_Weekly_FallsOnEveryMatchingDayInTheMonth(self):
        # 2026-01-02 is a Friday; the Fridays of September 2026 are the 4th, 11th, 18th, and 25th.
        found = september(window("weekly", 4, from_day=date(2026, 1, 2)))

        assert found == [date(2026, 9, 4), date(2026, 9, 11), date(2026, 9, 18), date(2026, 9, 25)]

    def test_DueDays_Fortnightly_KeepsThePhaseOfTheWindowsFirstDay(self):
        # From 2026-08-28 every fortnight: 09-11, 09-25.
        found = september(window("fortnightly", 4, from_day=date(2026, 8, 28)))

        assert found == [date(2026, 9, 11), date(2026, 9, 25)]

    def test_DueDays_WhenTheWindowClosedMidMonth_StopsAtTheLastDay(self):
        found = september(window("weekly", 4, from_day=date(2026, 1, 2), to_day=date(2026, 9, 15)))

        assert found == [date(2026, 9, 4), date(2026, 9, 11)]

    def test_DueDays_WhenTheWindowBeginsAfterTheDayOfTheMonth_HasNoneThatMonth(self):
        assert september(window("monthly", 3, from_day=date(2026, 9, 10))) == []

    def test_DueDays_AgreeWithTheDayPositionPlacesTheNextPayment(self):
        w = window("monthly", 20, from_day=date(2026, 1, 1))
        today = date(2026, 9, 15)

        assert due_days(w, today, date(2026, 10, 31))[0] == next_due(w, today)

    def test_DueDays_Yearly_FallsOnlyInItsMonth(self):
        yearly = replace(window("yearly", 14, from_day=date(2025, 10, 14)), usual_month=10)

        assert due_days(yearly, date(2026, 9, 1), date(2026, 9, 30)) == []
        assert due_days(yearly, date(2026, 10, 1), date(2026, 10, 31)) == [date(2026, 10, 14)]


class TestStateOfADueDay:
    TODAY = date(2026, 9, 15)
    W = window("monthly", 5, from_day=date(2026, 1, 1))

    def test_State_WhenAPaymentWasSeenNearTheDay_IsPaidOnThatDay(self):
        found = series(seen_days=(date(2026, 9, 3),))

        assert _state(date(2026, 9, 5), self.W, found, "2026-09-12", self.TODAY) == (
            PAID, "2026-09-03", "Paid 2026-09-03."
        )

    def test_State_WhenThePaymentWasSeenBeyondTheToleranceOfTheDay_IsNotPaid(self):
        found = series(seen_days=(date(2026, 8, 31),))

        assert _state(date(2026, 9, 5), self.W, found, "2026-09-12", self.TODAY)[0] == OVERDUE

    def test_State_WhenThePaymentIsDatedAfterToday_IsNotCountedAsSeen(self):
        found = series(seen_days=(date(2026, 9, 16),))

        assert _state(date(2026, 9, 17), self.W, found, "2026-09-12", self.TODAY)[0] == DUE

    def test_State_WhenTheDayIsStillInsideItsTolerance_IsDue(self):
        assert _state(date(2026, 9, 12), self.W, series(), "2026-09-12", self.TODAY)[0] == DUE

    def test_State_WhenTheDayPlusToleranceIsToday_IsStillDue(self):
        assert _state(date(2026, 9, 11), self.W, series(), "2026-09-15", self.TODAY)[0] == DUE

    def test_State_WhenTheDayPlusToleranceHasPassedAndTheRowsReachPastIt_IsOverdue(self):
        assert _state(date(2026, 9, 5), self.W, series(), "2026-09-10", self.TODAY)[0] == OVERDUE

    def test_State_WhenTheRowsStopOnTheLastToleratedDay_IsStillNotProvenOverdue(self):
        assert _state(date(2026, 9, 5), self.W, series(), "2026-09-08", self.TODAY)[0] == DUE

    def test_State_WhenNoRowsAreHeld_IsDueAndSaysWhetherItWasPaidIsNotKnown(self):
        state, _, said = _state(date(2026, 9, 5), self.W, series(), "", self.TODAY)

        assert state == DUE
        assert "not known" in said

    def test_State_WhenThePulledCardOwedNothingForTheCycle_IsNotTakenAndNotOverdue(self):
        found = series(explained=1, stopped=False, next_expected=date(2026, 9, 5))

        state, _, said = _state(date(2026, 9, 5), self.W, found, "2026-09-12", self.TODAY)

        assert state == NOT_TAKEN
        assert "owed nothing" in said

    def test_State_WhenTheSeriesIsExplainedButStopped_IsOverdue(self):
        found = series(explained=1, stopped=True)

        assert _state(date(2026, 9, 5), self.W, found, "2026-09-12", self.TODAY)[0] == OVERDUE

    def test_State_WhenNoSeriesMatches_IsOverdueAndSaysNoPaymentIsFound(self):
        state, _, said = _state(date(2026, 9, 5), self.W, None, "2026-09-12", self.TODAY)

        assert state == OVERDUE
        assert said == "No payment of it is found in the transactions held."
