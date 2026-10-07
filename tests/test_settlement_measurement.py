"""What Identity health says of where the export's rows sit against the settlement day.

Every household is invented (`consecutive_days_corpus`, `late_settlement_corpus`), landed through
the real doors in the order the deployed store's artefacts arrived in (the feed, then the
aggregator, then the export), live and rebuilt, and with a second export of an overlapping
span and the feed fetched again. The answers were decided before the first run.

KNOWN ANSWERS, by household (read from a store built WITHOUT the settlement rule, which is
what the measurement exists to describe; `test_settlement_rule` reads it again with the rule):

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
from dataclasses import replace
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
from obdi.ingest import matching
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
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


@pytest.fixture
def without_the_rule(monkeypatch):
    """The matcher as it was before a record listed on its settlement day was planned.

    The measurement says what the rule WOULD do to a store built without it, so the
    store these answers are read from must be built without it. `test_settlement_rule`
    asserts that the same measurement says none once the rule is applied.
    """
    monkeypatch.setattr(matching, "plan_settlement", lambda batch, index, **kwargs: {})


def figures_of(store: Store) -> SettlementFigures:
    (found,) = [f for f in exact_rule_report(store, MAP).settlement if f.account == MAIN]
    return found


@pytest.mark.usefixtures("without_the_rule")
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

    def test_Measurement_WhenTheRowsSwapDatesAndAreOfOneSize_NoDayHoldsAnotherTotal(
        self, stores, rebuild, again
    ):
        figures = figures_of(stores(consecutive_payments(7), rebuild=rebuild, again=again))

        assert figures.days_changed == 0
        assert figures.in_protected == 0
        assert figures.unreproduced == 0


def week_without_its_last_export_row() -> list[Payment]:
    week = consecutive_payments(7)
    return [*week[:-1], replace(week[-1], listed=None)]


@pytest.mark.usefixtures("without_the_rule")
@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
class TestAChainThatIsBrokenAtItsEnd:
    """The week, but the export never lists the last payment's settlement day.

    KNOWN ANSWER, worked by hand before the first run: six export rows on the transactions
    made on their own dates, so six moved, to the six payments before them; the first
    payment's date goes from the 15th to the 16th and so on to the sixth's from the 20th to
    the 21st, six re-dated; the last payment keeps the 21st, so the 15th loses its payment
    and the 21st gains one: two days with a different total. Measured: 6, 6, and 2.
    """

    def test_Measurement_WhenTheChainIsBroken_CountsTheDaysThatEndUpDifferent(
        self, stores, rebuild, again
    ):
        figures = figures_of(
            stores(week_without_its_last_export_row(), rebuild=rebuild, again=again)
        )

        assert figures.moved == 6
        assert figures.dates_changed == 6
        assert figures.days_changed == 2
        assert figures.unreproduced == 0


def bakery_week(*, broken: bool) -> list[Payment]:
    """A second payee at a second size, on the same days, in a chain of its own."""
    payments = [
        Payment(
            f"f-bread-{day}",
            "Bakery Co",
            770,
            f"2026-09-{day:02}T10:00:00.000Z",
            f"2026-09-{day + 1:02}T03:00:00.000Z",
            date(2026, 9, day + 1),
            day,
        )
        for day in range(15, 22)
    ]
    return [*payments[:-1], replace(payments[-1], listed=None)] if broken else payments


@pytest.mark.usefixtures("without_the_rule")
@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
class TestWhichMovesAreSafe:
    """A group of moves is safe when no day would hold a different total afterwards.

    KNOWN ANSWERS, worked by hand before the first run:
      a closed chain (the week: every export row on the next payment, the last on the first)
        one group of seven moves, safe, re-dating seven transactions, no day changing
      a broken chain (the last payment's export row missing)
        one group of six, unsafe, the 15th and the 21st changing, an open chain no export row
        would close
      the two together, in two payees of two sizes
        two groups of more than five, one safe (seven moves, seven re-dated) and one not
        (six moves, two days)
    """

    def test_Groups_WhenTheChainIsClosed_AreOneSafeGroupThatChangesNoDay(
        self, stores, rebuild, again
    ):
        figures = figures_of(stores(consecutive_payments(7), rebuild=rebuild, again=again))

        assert figures.group_count == 1
        assert figures.groups_of_more == 1
        assert figures.groups_safe == 1
        assert figures.groups_safe_balance_changed == 0
        assert (figures.moves_safe, figures.redated_safe, figures.moves_unsafe) == (7, 7, 0)
        assert figures.unsafe_days == []

    def test_Groups_WhenTheChainIsBroken_AreOneUnsafeGroupAndNameItsDays(
        self, stores, rebuild, again
    ):
        figures = figures_of(
            stores(week_without_its_last_export_row(), rebuild=rebuild, again=again)
        )

        assert (figures.group_count, figures.groups_safe) == (1, 0)
        assert (figures.moves_safe, figures.moves_unsafe) == (0, 6)
        assert figures.unsafe_days == [date(2026, 9, 15), date(2026, 9, 21)]
        assert figures.unsafe_open_unclosed == 1
        assert figures.unsafe_open_refused == 0
        assert figures.unsafe_other == 0

    def test_Groups_WhenOneChainIsClosedAndOneBroken_AreSplitAndEachJudgedAlone(
        self, stores, rebuild, again
    ):
        figures = figures_of(
            stores(
                [*consecutive_payments(7), *bakery_week(broken=True)],
                rebuild=rebuild,
                again=again,
            )
        )

        assert (figures.group_count, figures.groups_of_more) == (2, 2)
        assert figures.groups_safe == 1
        assert (figures.moves_safe, figures.redated_safe, figures.moves_unsafe) == (7, 7, 6)
        assert figures.unsafe_days == [date(2026, 9, 15), date(2026, 9, 21)]


@pytest.mark.usefixtures("without_the_rule")
class TestAChainWhoseClosingRowThePlanRefuses:
    """The week, plus a second export row of the last row's size and date.

    Two export rows then name the one transaction settled that day, so the plan refuses both
    (`sharing_one_candidate`) and the last link of the chain is never made. KNOWN ANSWER, by
    hand: six planned moves in one unsafe group, open, and the plan's refused rows are named as
    what would have closed it.
    """

    def test_Groups_WhenTheLastLinkIsRefused_SaysARefusedRowWouldHaveClosedIt(self, stores):
        twin = (("Coffee Co", -450, date(2026, 9, 22)),)

        figures = figures_of(stores(consecutive_payments(7), extra_listed=twin))

        assert figures.sharing_one_candidate == 2
        assert figures.groups_safe == 0
        assert figures.unsafe_open_refused == 1
        assert figures.unsafe_open_unclosed == 0


@pytest.mark.usefixtures("without_the_rule")
class TestRowsOnATransactionWithNoFeedSighting:
    """PREDICTED, before the first run: the household's row inside the feed's span, and the two
    rows added here, one a month before the feed's first item and one a month after its last,
    so three rows dated 2026-08-15 to 2026-10-05, one before, one after, and one inside."""

    def test_Sentence_WhenRowsSitOutsideAndInsideTheFeedsSpan_CountsEachByDate(self, stores):
        extra = (("Old", -999, date(2026, 8, 15)), ("Late", -888, date(2026, 10, 5)))

        figures = figures_of(stores(consecutive_payments(7), extra_listed=extra))
        text = "\n".join(figures.sentences())

        assert sorted(figures.none_no_feed_days) == [
            date(2026, 8, 15),
            date(2026, 9, 10),
            date(2026, 10, 5),
        ]
        assert "dated from 2026-08-15 to 2026-10-05" in text
        assert "the feed's items run from 2026-09-01 to 2026-09-21" in text
        assert "and 1 is dated before it, 1 after it, and 1 inside it." in text


class TestEveryExportRowIsAccountedFor:
    @pytest.mark.parametrize(
        "payments",
        [
            consecutive_payments(7),
            late_settlement_payments(),
            equal_payments(),
            equal_payments(both_listed_on_the_later_day=True),
            week_without_its_last_export_row(),
        ],
        ids=["week", "late", "equal", "equal-together", "broken-week"],
    )
    def test_Sentences_WhenEveryRowIsClassified_TheThreeKindsAddUpToTheRowsListed(
        self, stores, payments
    ):
        figures = figures_of(stores(payments))

        assert (
            figures.one_candidate + figures.several_candidates + figures.none_named
            == figures.export_rows
        )
        assert (
            figures.none_on_no_time
            + figures.none_on_no_feed
            + figures.none_on_other
            + figures.none_on_none
            == figures.none_named
        )

    def test_Measurement_WhenTheHouseholdRowsHaveNoSettlementDay_SaysWhereTheySit(self, stores):
        """PREDICTED: all seven household rows on a transaction whose feed sighting states no
        settlement time. MEASURED: six, and one on a transaction with no feed sighting, which
        was not examined further. The prediction was wrong, and this is what was found."""
        figures = figures_of(stores(consecutive_payments(7)))

        assert figures.none_named == 7
        assert figures.none_on_no_time == 6
        assert figures.none_on_no_feed == 1
        assert figures.none_on_none == 0


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
    @pytest.mark.usefixtures("without_the_rule")
    def test_Report_WhenTheWeekIsMeasured_StatesEachFigureInWordsAndNoValue(self, stores):
        text = exact_rule_report(stores(consecutive_payments(7)), MAP).describe()

        assert "7 name exactly one stored transaction by its settlement day." in text
        assert "Of those, 0 sit on that transaction and 7 sit on another transaction." in text
        assert "7 sit on one whose own feed report states a different settlement day" in text
        assert "would move 7 rows from one stored transaction to another." in text
        assert "7 stored transactions would carry another date as a result" in text
        assert "0 days would then hold a different total of counted transactions." in text
        assert "0 of the re-dated transactions carry a date, before or after" in text
        assert "7 name no stored transaction by their settlement day, which with the 7" in text
        for hidden in ("Coffee Co", "450", "4.50"):
            assert hidden not in text

    def test_Report_WhenNoExportIsLanded_SaysThereIsNothingToPlace(self, tmp_path):
        with Store(tmp_path / "empty.sqlite3") as store:
            text = exact_rule_report(store, MAP).describe()

        assert "No account has export rows landed, so there is nothing to place." in text
