"""Which date a rhythm is measured on: invented series whose two dates differ, answers first.

A habit's and a scheduled payment's rhythm is the day the owner acted (the transaction date,
`value_date`); a pulled payment's day is the day the collector took it (the posting date,
`booking_date`). Each answer below was decided from that rule before the detector was changed,
and the series says which date it used (`Series.dated_on`).

Calendar facts the answers lean on: 2026-10-07 is a Wednesday, so 2026-09-13 and 2026-05-03 are
Sundays, and a Sunday payment posts on the Monday after (2026-09-14) or the Tuesday.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

from obdi.analysis.recurring import (
    DATED_MADE,
    DATED_ONE,
    DATED_POSTED,
    DATED_TAKEN,
    HABIT,
    PULLED,
    SCHEDULED,
    Series,
    find_recurring,
)
from obdi.core.jsontypes import JsonObject
from obdi.core.models import SourceTier, Transaction

TODAY = date(2026, 10, 7)
ACCOUNT = "acct-a"
SUNDAY = 6

_counter = [0]


def row(
    made: date,
    posted: date,
    minor: int,
    description: str,
    *,
    source: str = "starling",
    raw: JsonObject | None = None,
) -> Transaction:
    _counter[0] += 1
    return Transaction(
        account_id=ACCOUNT,
        amount_minor=minor,
        value_date=made,
        booking_date=posted,
        description=description,
        source=source,
        tier=SourceTier.SYNTHETIC,
        entity_id=f"d{_counter[0]:06d}",
        raw=raw or {},
    )


def sundays(count: int, first: date = date(2026, 5, 3)) -> list[date]:
    return [first + timedelta(weeks=n) for n in range(count)]


def month_ends(count: int) -> list[tuple[date, date]]:
    """(last day of a month, the 1st that follows) for the `count` months to September 2026."""
    pairs = []
    for index in range(count):
        month = 9 - count + 1 + index  # a month number that may be zero or negative
        year = 2026 + (month - 1) // 12
        month = (month - 1) % 12 + 1
        following = date(year + (month == 12), month % 12 + 1, 1)
        pairs.append((following - timedelta(days=1), following))
    return pairs


def one(found: list[Series]) -> Series:
    assert len(found) == 1, [(s.label, s.cadence, s.usual_day) for s in found]
    return found[0]


class TestSundayHabit:
    def test_Habit_WhenFeedRowsPostOnMondaysAndTuesdays_IsStillMostSundays(self):
        rows = [
            row(day, day + timedelta(days=1 + n % 2), -450 - 10 * (n % 4), "SUNDAY BAKERY")
            for n, day in enumerate(sundays(20))
        ]

        series = one(find_recurring(rows, [], TODAY))

        assert (series.kind, series.cadence, series.weekday) == (HABIT, "weekly", SUNDAY)
        assert series.count == 20
        assert series.dated_on == DATED_MADE

    def test_Habit_WhenOnlyAPostingDateIsStated_IsAHabitOnThePostingWeekday(self):
        # The aggregator states one date, its posting date, so the owner's Sunday is not
        # knowable; the rows are all posted on a Monday, and the rhythm says so.
        rows = [
            row(day + timedelta(days=1), day + timedelta(days=1), -450, "SUNDAY BAKERY",
                source="truelayer")
            for day in sundays(20)
        ]

        series = one(find_recurring(rows, [], TODAY))

        assert (series.kind, series.cadence, series.weekday) == (HABIT, "weekly", 0)
        assert series.dated_on == DATED_POSTED

    def test_Habit_WhenOnlyAPostingDateIsStatedAndItSpreadsOverTwoDays_IsNotFound(self):
        # THE KNOWN LIMIT: a Sunday habit seen only through a feed that posts it on a Monday
        # or a Tuesday has no transaction date to fit, and the posting weekdays are split in
        # half. Not found beats a rhythm invented from days the owner did not choose.
        rows = [
            row(posted, posted, -450, "SUNDAY BAKERY", source="truelayer")
            for n, day in enumerate(sundays(20))
            for posted in [day + timedelta(days=1 + n % 2)]
        ]

        assert find_recurring(rows, [], TODAY) == []

    def test_Habit_WhenRowsStateOneDate_SaysItIsTheOneDate(self):
        rows = [row(day, day, -450, "SUNDAY BAKERY", source="synthetic") for day in sundays(20)]

        series = one(find_recurring(rows, [], TODAY))

        assert (series.kind, series.weekday) == (HABIT, SUNDAY)
        assert series.dated_on == DATED_ONE

    def test_Habit_WhenStatementAndFeedRowsAreMixed_IsOneSeriesOnSundays(self):
        days = sundays(12)
        feed = [row(day, day + timedelta(days=1), -450, "SUNDAY BAKERY") for day in days[:6]]
        statement = [
            row(day, day + timedelta(days=2), -450, "SUNDAY BAKERY", source="card-statement")
            for day in days[6:]
        ]

        series = one(find_recurring(feed + statement, [], TODAY))

        assert (series.kind, series.weekday, series.count) == (HABIT, SUNDAY, 12)
        assert series.dated_on == DATED_MADE


class TestPulledPaymentTakenOnThe1st:
    def test_DirectDebit_WhenTransactionDatesAreTheLastDayOfThePreviousMonth_IsOnThe1st(self):
        rows = [
            row(made, taken, -4200 - n, "WATER BOARD", source="card-statement",
                raw={"source": "DIRECT_DEBIT"})
            for n, (made, taken) in enumerate(month_ends(6))
        ]

        series = one(find_recurring(rows, [], TODAY))

        assert (series.kind, series.cadence, series.usual_day) == (PULLED, "monthly", 1)
        assert series.dated_on == DATED_TAKEN
        assert series.next_expected == date(2026, 11, 1)
        assert not series.stopped

    def test_DirectDebit_WhenNoTypeIsStated_ShapeStillPlacesItOnThe1st(self):
        rows = [row(made, taken, -4200, "WATER BOARD") for made, taken in month_ends(6)]

        series = one(find_recurring(rows, [], TODAY))

        assert (series.kind, series.usual_day) == (PULLED, 1)
        assert series.dated_on == DATED_TAKEN

    def test_DirectDebit_WhenTransactionDatesScatterButItIsAlwaysTakenOnThe1st_IsOnThe1st(self):
        scatter = [20, 27, 22, 28, 21, 26]
        rows = [
            row(date(end.year, end.month, scatter[n]), taken, -4200, "WATER BOARD")
            for n, (end, taken) in enumerate(month_ends(6))
        ]

        series = one(find_recurring(rows, [], TODAY))

        assert (series.kind, series.usual_day, series.dated_on) == (PULLED, 1, DATED_TAKEN)

    def test_DirectDebit_WhenStatementAndAggregatorRowsAreMixed_IsOneSeriesOnThe1st(self):
        pairs = month_ends(12)
        statement = [
            row(made, taken, -4200, "WATER BOARD", source="card-statement")
            for made, taken in pairs[:6]
        ]
        aggregator = [
            row(taken, taken, -4200, "WATER BOARD", source="truelayer") for _, taken in pairs[6:]
        ]

        series = one(find_recurring(statement + aggregator, [], TODAY))

        assert (series.kind, series.cadence, series.usual_day) == (PULLED, "monthly", 1)
        assert series.count == 12
        assert series.dated_on == DATED_TAKEN


class TestScheduledPaymentMadeOnThe15th:
    def test_StandingTransfer_WhenItPostsTwoDaysLater_IsOnThe15th(self):
        # Scheduled by shape (a transfer between the owner's own accounts, same day each month);
        # the coded standing-order word is stated only by the aggregator, which states no
        # second date.
        rows = [
            replace(
                row(date(2026, month, 15), date(2026, month, 17), -50000, "TO SAVINGS POT"),
                is_internal_transfer=True,
            )
            for month in range(3, 10)
        ]

        series = one(find_recurring(rows, [], TODAY))

        assert (series.kind, series.usual_day) == (SCHEDULED, 15)
        assert series.dated_on == DATED_MADE
