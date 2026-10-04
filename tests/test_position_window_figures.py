"""The position's figures on any list of days, and the month-end chart unchanged.

The household and every figure it must give are worked out by hand in
`position_window_household`, before the first run. What is held here is:

* the month-end chart of everything is the same to the pence through the one path
  that takes days (the hand-worked household of `test_position_chart_choice`, with
  and without its asset);
* each kind of item gives the right figure on an arbitrary day: an account that
  opens partway through, a balance-only account stated twice, an asset valued
  inside the window, an account whose opening is not known;
* the cost is one pass over each account's rows however many days are asked for.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest

from obdi.balance_anchors import EffectiveOpening
from obdi.date_window import Resolution, sample_days
from obdi.models import SourceTier, Transaction, TransactionStatus
from obdi.position import (
    AccountInput,
    AssetInput,
    Observation,
    Position,
    build_position,
    chart_series,
    held_from,
    read_position,
    series_at,
    window_points,
)
from obdi.store import Store
from position_window_household import (
    CARD,
    EVERYDAY,
    PENSION,
    SAVER,
    TODAY,
    known_on,
    window_household,
)
from test_position_chart_choice import household, household_with_house

D = date


def daily(position: Position, first: date, last: date, drawn=None):
    days = sample_days(first, last, Resolution.DAY)
    return series_at(position, window_points(days, Resolution.DAY, TODAY), drawn)


@pytest.fixture
def held(tmp_path) -> Position:
    with Store(tmp_path / "w.sqlite3") as store:
        window_household(store)
        return read_position(store, labels={}, today=TODAY)


def by_day(series) -> dict[str, int]:
    return {p.month: p.net_worth.minor for p in series.history}


class TestTheMonthEndChartIsUnchanged:
    @pytest.mark.parametrize("build", [household, household_with_house])
    def test_MonthEnds_ThroughThePathThatTakesDays_AreTheHistoryToThePence(self, tmp_path, build):
        with Store(tmp_path / "p.sqlite3") as store:
            build(store)
            position = read_position(store, labels={}, today=date(2026, 10, 2))
        today = date(2026, 10, 2)
        days = sample_days(date(2026, 3, 1), today, Resolution.MONTH)

        drawn = series_at(position, window_points(days, Resolution.MONTH, today), None)

        assert [p.month for p in drawn.history] == [p.month for p in position.history]
        assert drawn.history == position.history
        assert drawn.complete_from == position.complete_from
        assert drawn.provisional == position.provisional_history

    def test_MonthEnds_OfTheWindowHousehold_AreTheHandWorkedFigures(self, held):
        days = sample_days(D(2026, 6, 1), TODAY, Resolution.MONTH)

        drawn = series_at(held, window_points(days, Resolution.MONTH, TODAY), None)

        assert by_day(drawn) == {
            "2026-06": 280000, "2026-07": 280000, "2026-08": 520000,
            "2026-09": 710000, "2026-10": 705000,
        }
        assert drawn.history == held.history
        assert drawn.complete_from == "2026-09"

    def test_ChosenItems_OverTheMonthEnds_StillMatchTheirOwnFigures(self, tmp_path):
        with Store(tmp_path / "p.sqlite3") as store:
            household(store)
            position = read_position(store, labels={}, today=date(2026, 10, 2))
        rest = {i.key for i in position.chart_items} - {"account:hsbc-mortgage"}

        drawn = chart_series(position, rest)

        assert [p.net_worth.minor for p in drawn.history][:3] == [45000, 140000, 162000]


class TestEachKindOfItemOnAnArbitraryDay:
    def test_Daily_OverTheHandWorkedHousehold_GivesTheWorkedTotalOnEveryDay(self, held):
        series = daily(held, D(2026, 7, 7), TODAY)

        figures = by_day(series)
        assert len(figures) == 90
        for day in sample_days(D(2026, 7, 7), TODAY, Resolution.DAY):
            assert figures[day.isoformat()] == known_on(day), day

    def test_AccountOpeningMidWindow_IsLeftOutBeforeItsOpeningAndMarksThosePointsPartial(
        self, held
    ):
        series = daily(held, D(2026, 9, 10), D(2026, 9, 16))
        counted = {p.month: (p.included, p.partial) for p in series.history}

        assert counted["2026-09-10"] == (3, True)
        assert counted["2026-09-11"] == (4, True)
        assert counted["2026-09-13"] == (4, True)
        assert counted["2026-09-14"] == (5, False)
        assert counted["2026-09-16"] == (5, False)
        assert series.complete_from == "2026-09-14"

    def test_BalanceOnlyAccountStatedTwice_StepsOnTheSecondStatedDayAndNotBefore(self, held):
        only_pension = {PENSION}

        series = daily(held, D(2026, 7, 30), D(2026, 9, 16), only_pension)

        figures = by_day(series)
        assert "2026-07-31" not in figures
        assert figures["2026-08-01"] == 200000
        assert figures["2026-09-14"] == 200000
        assert figures["2026-09-15"] == 230000

    def test_AssetValuedInsideTheWindow_IsCarriedForwardUntilItsNextObservation(self, held):
        series = daily(held, D(2026, 8, 30), D(2026, 9, 2), {"asset:bond"})

        assert by_day(series) == {
            "2026-08-30": 280000, "2026-08-31": 280000, "2026-09-01": 320000, "2026-09-02": 320000,
        }

    def test_AssetObservedBeforeTheWindow_IsStillItsFigureAtTheWindowsFirstDay(self, held):
        series = daily(held, D(2026, 7, 7), D(2026, 7, 8), {"asset:bond"})

        assert by_day(series) == {"2026-07-07": 280000, "2026-07-08": 280000}

    def test_UncountedAccount_MovesOnlyTheProvisionalLineFromItsFirstRow(self, held):
        series = daily(held, D(2026, 8, 18), D(2026, 8, 21))

        known = by_day(series)
        guess = {p.month: p.total.minor for p in series.provisional}
        assert known == {
            "2026-08-18": 520000, "2026-08-19": 520000, "2026-08-20": 520000, "2026-08-21": 520000,
        }
        assert guess["2026-08-19"] == 520000
        assert guess["2026-08-20"] == 527000

    def test_UncountedAccount_AfterItsSecondRow_MovesTheProvisionalLineDown(self, held):
        series = daily(held, D(2026, 9, 17), D(2026, 9, 18))

        guess = {p.month: p.total.minor for p in series.provisional}
        assert guess == {"2026-09-17": -103000, "2026-09-18": -105000}

    def test_CardPurchase_TakesTheHouseholdBelowNilForExactlyTheDaysItIsOwed(self, held):
        series = daily(held, D(2026, 9, 1), TODAY)

        below = [p.month for p in series.history if p.net_worth.minor < 0]
        assert below[0] == "2026-09-12" and below[-1] == "2026-09-25"
        assert len(below) == 14

    def test_CardLeftOut_TheSameDaysNeverGoBelowNil(self, held):
        rest = {i.key for i in held.chart_items} - {CARD}

        series = daily(held, D(2026, 9, 1), TODAY, rest)

        assert min(p.net_worth.minor for p in series.history) > 0

    def test_WindowEndingToday_ReadsItsLastDayAsEverythingHeldNow(self, held):
        series = daily(held, D(2026, 9, 28), TODAY)

        assert held.net_worth is not None
        assert series.history[-1].net_worth.minor == held.net_worth.minor == 705000

    def test_WindowEndingInThePast_ReadsItsLastDayAtThatDay(self, held):
        days = sample_days(D(2026, 9, 20), D(2026, 9, 25), Resolution.DAY)

        series = series_at(held, window_points(days, Resolution.DAY, TODAY), None)

        assert series.history[-1].month == "2026-09-25"
        assert series.history[-1].net_worth.minor == -90000

    def test_WindowBeforeAnythingIsHeld_HasNoPointsAndNoLine(self, held):
        series = daily(held, D(2026, 1, 1), D(2026, 1, 31))

        assert series.history == () and series.provisional == ()

    def test_WindowFromTheFirstDayHeld_StartsOnItsFirstFigure(self, held):
        assert held_from(held) == D(2026, 6, 15)

    def test_ItemsNotChosen_AreLeftOutOfEveryDay(self, held):
        series = daily(held, D(2026, 9, 28), D(2026, 9, 29), {EVERYDAY, SAVER})

        assert by_day(series) == {"2026-09-28": 160000, "2026-09-29": 160000}

    def test_NothingChosen_DrawsNothing(self, held):
        assert daily(held, D(2026, 9, 28), TODAY, set()).history == ()


def counted_account(ref: str, rows: int, first: date) -> AccountInput:
    items = tuple(
        Transaction(
            account_id=ref,
            amount_minor=100 + n,
            value_date=first + timedelta(days=n % 700),
            booking_date=first + timedelta(days=n % 700),
            description="x",
            source="s",
            source_id=f"{ref}-{n}",
            content_key=f"{ref}-{n}",
            tier=SourceTier.AUTHORITATIVE,
            status=TransactionStatus.BOOKED,
            entity_id=f"{ref}-{n}",
        )
        for n in range(rows)
    )
    return AccountInput(
        ref=ref, label=ref, kind="", archived=False,
        opening=EffectiveOpening(ref, (), 1000, first - timedelta(days=1), 0),
        rows=items,
    )


def timed(rows_each: int, days: int) -> float:
    accounts = [counted_account(f"a{n}", rows_each, D(2024, 11, 1)) for n in range(19)]
    asset = AssetInput("bond", (Observation(D(2024, 12, 1), "investment", 5000, None, "s"),))
    runs = []
    for _ in range(3):
        start = time.perf_counter()
        position = build_position(accounts, [asset], today=D(2026, 10, 4))
        every_day = [D(2024, 11, 2) + timedelta(days=n % 700) for n in range(days)]
        series_at(position, window_points(every_day, Resolution.DAY, D(2026, 10, 4)), None)
        runs.append(time.perf_counter() - start)
    return min(runs)


class TestTheCostOfAChart:
    def test_Chart_WhenRowsAndDaysBothQuadruple_CostsAboutFourTimesAsMuch(self):
        """Nineteen accounts, as the real household has, about 5,300 rows at the larger size.

        One pass over each account's rows plus a lookup per day is four times the work
        when both quadruple; a pass over every row for every day is sixteen times. The
        bound is the minimum of three runs and sits between the two, as in
        `test_family_scaling`. Measured: 3.5 as built, 9.5 when each day re-counts the rows.
        """
        small = timed(70, 100)
        large = timed(280, 400)

        assert large / small < 7, f"{small:.4f}s became {large:.4f}s, x{large / small:.1f}"

    def test_Chart_WhenOnlyTheDaysGrow_DoesNotReReadTheRows(self):
        few = timed(280, 20)
        many = timed(280, 700)

        # Lookups are cheap beside building the position, so 35 times the days is
        # nowhere near 35 times the cost. Measured: about 2.4 as built, and 10.4 when
        # each day re-counts the rows, so the bound sits between.
        assert many / few < 6, f"{few:.4f}s became {many:.4f}s, x{many / few:.1f}"
