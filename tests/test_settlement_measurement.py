"""What Identity health says of where the export's rows sit against the settlement day.

Every household is invented (`consecutive_days_corpus`, `late_settlement_corpus`), landed through
the real doors in the order the deployed store's artefacts arrived in (the feed, then the
aggregator, then the export), live and rebuilt, and with a second export of an overlapping
span and the feed fetched again. The answers were decided before the first run.

KNOWN ANSWERS, by household:

    a week of one payee paid one amount on consecutive days, each settling the day after
        seven export rows, each naming exactly one stored transaction by its settlement day
        none sits on that transaction: every row sits on the transaction MADE on its date
            (or, for the last, on the first), each of which states another settlement day
        none sits on no transaction
        the rule would move seven rows, and seven stored transactions would change date
    late settlement (six payments the matcher already joins by settlement)
        six export rows name exactly one, all six sit on it; nothing would move or change date
    two equal payments of one payee settled on one day, both listed on it
        two export rows each naming two: one set of two, assignable; nothing moves
    two equal payments of one payee made on consecutive days, settled on one day
        the same, and both rows are of a payee paid the same size on consecutive days
    two equal payments settled on one day, one export row
        one export row naming two: a set with fewer rows than transactions
    one payment settled on a day, two export rows of its size on it
        two rows each naming one, sharing it: the rule leaves both to the existing order
"""

from __future__ import annotations

import pathlib
from collections.abc import Callable, Iterator
from datetime import date

import pytest

from consecutive_days_corpus import consecutive_payments
from late_settlement_corpus import (
    Payment,
    equal_payments,
    household,
    late_settlement_payments,
)
from obdi.exact_rule_measure import SettlementFigures, exact_rule_report
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store
from test_absorbed_rows import second_export
from test_consecutive_days_nothing_joined import repeat_the_feed
from test_space_attribution import MAIN, MAP

#: The deployed store's arrival order, and the one a rebuild of it replays.
REAL_ORDER = ("feed", "aggregator", "export")
SHAPES = [(False, False), (False, True), (True, False), (True, True)]
SHAPE_IDS = ["live", "live-again", "rebuilt", "rebuilt-again"]


@pytest.fixture
def stores(tmp_path) -> Iterator[Callable[..., Store]]:
    opened: list[Store] = []

    def build(payments, *, rebuild=False, again=False, order=REAL_ORDER, **kwargs) -> Store:
        here: pathlib.Path = tmp_path / f"d{len(opened)}"
        here.mkdir()
        store = household(here, order, payments, linked=True, **kwargs)
        opened.append(store)
        if again:
            repeat_the_feed(store, payments)
            second_export(store, here, payments)
        if rebuild:
            assert rebuild_from_raw(store, account_map=MAP).problems == []
        return store

    yield build
    for store in opened:
        store.close()


def figures_of(store: Store) -> SettlementFigures:
    (found,) = [f for f in exact_rule_report(store, MAP).settlement if f.account == MAIN]
    return found


@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
class TestAWeekOfEqualPaymentsOnConsecutiveDays:
    def test_Measurement_WhenEachRowSitsOnTheNextPayment_NamesEveryRowAsMoved(
        self, stores, rebuild, again
    ):
        figures = figures_of(stores(consecutive_payments(7), rebuild=rebuild, again=again))

        assert figures.one_candidate == 7
        assert figures.on_that_transaction == 0
        assert figures.on_another == 7
        assert figures.on_another_other_day == 7
        assert figures.on_none == 0
        assert figures.several_candidates == 0
        assert figures.sharing_one_candidate == 0
        assert figures.moved == 7
        assert figures.dates_changed == 7


@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
class TestPaymentsTheMatcherAlreadyJoinsBySettlement:
    def test_Measurement_WhenEveryRowSitsOnItsPayment_MovesNothingAndChangesNoDate(
        self, stores, rebuild, again
    ):
        figures = figures_of(stores(late_settlement_payments(), rebuild=rebuild, again=again))

        assert figures.one_candidate == 6
        assert figures.on_that_transaction == 6
        assert figures.on_another == 0
        assert figures.on_none == 0
        assert figures.moved == 0
        assert figures.dates_changed == 0

    def test_Measurement_WhenTwoEqualPaymentsAreListedOnOneDay_ReportsOneAssignableSet(
        self, stores, rebuild, again
    ):
        figures = figures_of(
            stores(equal_payments(both_listed_on_the_later_day=True), rebuild=rebuild, again=again)
        )

        assert figures.several_candidates == 2
        assert figures.candidates_two == 2
        assert figures.candidates_three == 0
        assert figures.sets_assignable == 1
        assert figures.moved == 0
        assert figures.dates_changed == 0


SETTLED = "2026-09-17T03:00:00.000Z"


def pair_settling_together(*, listed_second: bool = True) -> list[Payment]:
    return [
        Payment("f-pair-1", "Gym", 700, "2026-09-15T10:00:00.000Z", SETTLED, date(2026, 9, 17), 15),
        Payment(
            "f-pair-2",
            "Gym",
            700,
            "2026-09-16T10:00:00.000Z",
            SETTLED,
            date(2026, 9, 17) if listed_second else None,
            16,
        ),
    ]


class TestSetsOfOneSizeAndDate:
    def test_Measurement_WhenEqualPaymentsAreMadeOnConsecutiveDays_SaysTheyAreInARun(self, stores):
        figures = figures_of(stores(pair_settling_together()))

        assert figures.several_candidates == 2
        assert figures.in_a_run == 2
        assert figures.sets_assignable == 1

    def test_Measurement_WhenEqualPaymentsAreNotOnConsecutiveDays_SaysNoneIsInARun(self, stores):
        figures = figures_of(stores(equal_payments(both_listed_on_the_later_day=True)))

        assert figures.in_a_run == 0

    def test_Measurement_WhenTheExportListsFewerRowsThanThereAreTransactions_RefusesTheSet(
        self, stores
    ):
        figures = figures_of(stores(pair_settling_together(listed_second=False)))

        assert figures.several_candidates == 1
        assert figures.sets_fewer_rows_than_candidates == 1
        assert figures.sets_assignable == 0
        assert figures.moved == 0

    def test_Measurement_WhenThreeEqualPaymentsSettleTogether_CountsThreeCandidatesEach(
        self, stores
    ):
        trio = [
            Payment(
                f"f-trio-{n}", "Gym", 700, f"2026-09-1{n}T10:00:00.000Z", SETTLED,
                date(2026, 9, 17), 10 + n,
            )
            for n in (3, 4, 5)
        ]

        figures = figures_of(stores(trio))

        assert figures.candidates_three == 3
        assert figures.candidates_two == 0
        assert figures.sets_assignable == 1

    def test_Measurement_WhenFourEqualPaymentsSettleTogether_CountsFourOrMore(self, stores):
        quartet = [
            Payment(
                f"f-quartet-{n}", "Gym", 700, f"2026-09-1{n}T10:00:00.000Z", SETTLED,
                date(2026, 9, 17), 10 + n,
            )
            for n in (2, 3, 4, 5)
        ]

        figures = figures_of(stores(quartet))

        assert figures.candidates_four_or_more == 4
        assert figures.sets_assignable == 1


class TestRowsThatShareTheirOneTransaction:
    def test_Measurement_WhenTwoRowsNameOneTransaction_BothAreLeftToTheExistingOrder(
        self, stores
    ):
        lone = Payment(
            "f-lone", "Abroad Shop", 3333, "2026-09-14T10:00:00.000Z",
            "2026-09-15T03:00:00.000Z", date(2026, 9, 15), 14,
        )
        twin = (("Abroad Shop", -3333, date(2026, 9, 15)),)

        figures = figures_of(stores([lone], extra_listed=twin))

        assert figures.one_candidate == 2
        assert figures.sharing_one_candidate == 2
        assert figures.moved == 0

    def test_Measurement_WhenEachRowNamesATransactionOfItsOwn_NoRowIsShared(self, stores):
        figures = figures_of(stores(late_settlement_payments()))

        assert figures.sharing_one_candidate == 0


class TestTheSentences:
    def test_Report_WhenTheWeekIsMeasured_StatesEachFigureInWordsAndNoValue(self, stores):
        text = exact_rule_report(stores(consecutive_payments(7)), MAP).describe()

        assert "7 name exactly one stored transaction by its settlement day." in text
        assert "Of those, 0 sit on that transaction and 7 sit on another transaction." in text
        assert "7 sit on one whose own feed sighting states a different settlement day" in text
        assert "would move 7 rows from one stored transaction to another." in text
        assert "7 stored transactions would carry another date as a result" in text
        for hidden in ("Coffee Co", "450", "4.50"):
            assert hidden not in text

    def test_Report_WhenNoExportIsLanded_SaysThereIsNothingToPlace(self, tmp_path):
        with Store(tmp_path / "empty.sqlite3") as store:
            text = exact_rule_report(store, MAP).describe()

        assert "No account has export rows landed, so there is nothing to place." in text
