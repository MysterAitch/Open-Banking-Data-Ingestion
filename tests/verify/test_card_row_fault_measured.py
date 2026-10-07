"""A card's count fault says whether its known balances are met with the rows as held.

The deployed page said, of a card fed by an aggregator and by statements:

    2025-11-01 ... via truelayer (out): 2 rows of one size and direction listed, 1 held: both
    listed rows are sighted on one stored row, dated 2025-10-30

and could not say whether the store had lost a payment or the aggregator had listed one twice.
The cause of that shape is NOT reproduced here: through the real importers, in every order
tried, two aggregator items with ids of their own never end up sighted on one stored row. What
is built here is the arithmetic that decides, which the next deploy can read off the real page.

The corpus and its answers are in `card_two_ids_corpus`. Every scenario runs in both arrival
orders, live and rebuilt, and again with a second aggregator artefact that lists the same rows
again (an overlapping fetch).
"""

from __future__ import annotations

from datetime import date

import pytest

from card_two_ids_corpus import (
    FIRST_CLOSING,
    SECOND_CLOSING,
    bakery,
    card_store,
    land_aggregator,
    payment,
    post_office,
)
from obdi.ingest.store import Store
from obdi.verify.agreement import (
    AGREES,
    DEFINES,
    HELD_MOVEMENT,
    MET,
    Fault,
    Known,
    derive_agreement,
)
from obdi.verify.movement_completeness import check_rows

ORDERS = [("statements", "aggregator"), ("aggregator", "statements")]
SHAPES = [(False, False), (False, True), (True, False), (True, True)]
SHAPE_IDS = ["live", "rebuilt", "live-again", "rebuilt-again"]

BETWEEN = f"the known balances of {FIRST_CLOSING.isoformat()} and {SECOND_CLOSING.isoformat()}"
LINE = (
    "2025-10-30 invented-card via truelayer (out): 1 row of one size and direction listed, "
    "2 held"
)


def canonical(ref: str) -> str:
    return ref


def two_fetches(*, again: bool) -> list[list[dict]]:
    fetches = [
        [post_office(), payment("tl-garage-1")],
        [post_office(), bakery(), payment("tl-garage-2")],
    ]
    if again:
        # The same rows once more in an artefact of its own, so the bytes differ.
        fetches.append([bakery(), post_office(), payment("tl-garage-1")])
    return fetches


def said(store: Store) -> list[str]:
    return [fault.says() for fault in check_rows(store, canonical).row_faults]


@pytest.fixture
def made(tmp_path):
    opened: list[Store] = []

    def build(order, **kwargs) -> Store:
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        store = card_store(directory, order, **kwargs)
        opened.append(store)
        return store

    yield build
    for store in opened:
        store.close()


@pytest.mark.parametrize("order", ORDERS, ids=["statements-first", "aggregator-first"])
@pytest.mark.parametrize(("again", "rebuild"), SHAPES, ids=SHAPE_IDS)
class TestWhichCountTheKnownBalancesSupport:
    def test_Fault_WhenTheStatementCountsOnePayment_SaysTheBalancesDifferByExactlyTheSurplusRow(
        self, made, order, again, rebuild
    ):
        store = made(
            order, payments_printed=1, fetches=two_fetches(again=again), rebuild=rebuild
        )

        (line,) = said(store)

        assert line.startswith(LINE)
        assert line.endswith(f"; {BETWEEN} differ from the rows as held by exactly the surplus row")

    def test_Fault_WhenTheStatementCountsTwoPayments_SaysTheBalancesAreMetAsHeld(
        self, made, order, again, rebuild
    ):
        store = made(
            order, payments_printed=2, fetches=two_fetches(again=again), rebuild=rebuild
        )

        (line,) = said(store)

        assert line.startswith(LINE)
        assert line.endswith(
            f"; {BETWEEN} are met by the rows as held, and with 1 fewer row of that size and "
            "direction they would differ by exactly it"
        )


class TestWhatTheMeasurementDoesNotSay:
    def test_Fault_WhenNoKnownBalanceStandsAfterTheDay_SaysItCannotSay(self, tmp_path):
        store = Store(tmp_path / "bare.sqlite3")
        try:
            land_aggregator(store, [payment("tl-garage-1")])
            land_aggregator(store, [payment("tl-garage-2"), post_office()])

            (line,) = said(store)

            assert line.endswith(
                "; the known balances do not stand on both sides of the day, so they cannot say"
            )
        finally:
            store.close()

    def test_Report_WhenTheCountsAgree_SaysNothingOfBalances(self, made):
        store = made(
            ORDERS[0],
            payments_printed=1,
            fetches=[[post_office(), bakery(), payment("tl-garage-1")]],
        )

        assert said(store) == []


class TestWhatAFaultBetweenTwoMetBalancesDoesToAgreement:
    """The rule as written, pinned: a movement fault holds back every known balance from its day.

    The deployed page read "in agreement through no date yet" with known balances from
    2025-10-10 met, and a count fault dated 2025-11-01. The first known balance is the one that
    DEFINES the opening and tests nothing, so every balance that tests the rows is dated on or
    after the fault, and none is counted (`agreement`, rule 4).
    """

    def test_Agreement_WhenTheOnlyBalanceBeforeAFaultDefinesTheOpening_IsInAgreementThroughNoDate(
        self,
    ):
        known = [
            Known(date(2025, 10, 10), "statement", DEFINES, 1),
            Known(date(2025, 11, 10), "statement", MET, 2),
            Known(date(2025, 12, 10), "statement", MET, 3),
        ]

        agreement = derive_agreement(known, [Fault(date(2025, 11, 1), "a count fault")])

        assert agreement.through is None
        assert agreement.state == HELD_MOVEMENT

    def test_Agreement_WhenTheFaultFollowsEveryMetBalance_IsInAgreementThroughTheLastOfThem(self):
        known = [
            Known(date(2025, 10, 10), "statement", DEFINES, 1),
            Known(date(2025, 11, 10), "statement", MET, 2),
        ]

        agreement = derive_agreement(known, [Fault(date(2025, 12, 1), "a count fault")])

        assert agreement.through == date(2025, 11, 10)
        assert agreement.state == AGREES


class TestWhatOneArtefactListsOfIds:
    """A listed-more-than-held fault says whether the rows come from one artefact, and their ids.

    The artefacts are landed and not derived, so nothing is held: the shape every artefact
    landed ahead of its import has.
    """

    def landed(self, tmp_path, *fetches):
        store = Store(tmp_path / "landed.sqlite3")
        for records in fetches:
            land_aggregator(store, records, derive=False)
        return store

    def test_Fault_WhenOneArtefactListsTwoItemsWithIdsOfTheirOwn_SaysTwoDistinctIds(
        self, tmp_path
    ):
        store = self.landed(tmp_path, [payment("tl-garage-1"), payment("tl-garage-2")])
        try:
            (line,) = said(store)

            assert "2 rows of one size and direction listed, 0 held" in line
            assert "listed by 1 artefact, stating 2 distinct ids" in line
        finally:
            store.close()

    def test_Fault_WhenTwoArtefactsListOneItemEachUnderOneId_SaysOneDistinctId(self, tmp_path):
        store = self.landed(
            tmp_path,
            [payment("tl-garage-1")],
            [payment("tl-garage-1"), post_office()],
        )
        try:
            lines = [line for line in said(store) if "2025-10-30" in line]

            assert len(lines) == 1
            assert "1 row of one size and direction listed, 0 held" in lines[0]
            assert "listed by 2 artefacts, stating 1 distinct id" in lines[0]
        finally:
            store.close()

    def test_Fault_WhenTwoArtefactsEachListOneItemUnderADifferentId_SaysTwoDistinctIds(
        self, tmp_path
    ):
        store = self.landed(
            tmp_path, [payment("tl-garage-1")], [payment("tl-garage-2"), post_office()]
        )
        try:
            lines = [line for line in said(store) if "2025-10-30" in line]

            assert "1 row of one size and direction listed, 0 held" in lines[0]
            assert "listed by 2 artefacts, stating 2 distinct ids" in lines[0]
        finally:
            store.close()

    def test_Fault_WhenTheItemsStateNoId_SaysTheyStateNone(self, tmp_path):
        store = self.landed(tmp_path, [payment(None), payment(None)])
        try:
            (line,) = said(store)

            assert "listed by 1 artefact, stating no id" in line
        finally:
            store.close()
