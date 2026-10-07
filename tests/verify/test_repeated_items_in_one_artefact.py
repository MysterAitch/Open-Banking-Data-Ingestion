"""An item a source lists twice under one id in one artefact is one listed row.

The deployed page said, of a card fed by an aggregator and by statements:

    2025-11-01 santander-all-in-one-credit-card via truelayer (out): 2 rows of one size and
    direction listed, 1 held: both listed rows are sighted on one stored row, dated 2025-10-30;
    listed by 1 artefact, stating 1 distinct id; the known balances of 2025-10-10 and 2025-11-12
    are met by the rows as held, and with 1 more row of that size and direction they would differ
    by exactly it

One artefact listed one item twice under one id, and the store was right. Two items with different
ids of their own are two rows (`matching.could_be_one_payment` already reads one id as one payment
and two as two). Expected on that store after this change, and not known until it is read: the line
goes, the repeat is said once as information, and the account is no longer held back by it.

The corpus is `card_two_ids_corpus`. Every scenario runs in both arrival orders, live and rebuilt,
and again with a second aggregator artefact that lists the same rows again.

KNOWN ANSWERS, decided before the first run:

    a statement counting ONE payment, the aggregator listing it twice under one id in one artefact
        no fault; one sentence of information: "truelayer listed 1 item twice in one artefact on
        2025-10-30; it is one row"; the account is in agreement through the second closing
    the aggregator listing two ids of one size on one day in one artefact, nothing derived yet
        a fault: 2 listed, 0 held; the account is held back from the day
"""

from __future__ import annotations

import pytest

from card_two_ids_corpus import (
    CARD,
    SECOND_CLOSING,
    bakery,
    card_store,
    land_aggregator,
    payment,
    post_office,
)
from obdi.ingest.store import Store
from obdi.verify.agreement import AGREES, HELD_MOVEMENT, standing_of
from obdi.verify.balance_anchors import effective_opening
from obdi.verify.movement_completeness import check_rows, movement_completeness

ORDERS = [("statements", "aggregator"), ("aggregator", "statements")]
SHAPES = [(False, False), (False, True), (True, False), (True, True)]
SHAPE_IDS = ["live", "live-again", "rebuilt", "rebuilt-again"]
SAID_ONCE = "truelayer listed 1 item twice in one artefact on 2025-10-30; it is one row"


def canonical(ref: str) -> str:
    return ref


def listed_twice(*, again: bool) -> list[list[dict]]:
    fetches = [[post_office(), bakery(), payment("tl-garage-1"), payment("tl-garage-1")]]
    if again:
        # The same rows once more in an artefact of its own, so the bytes differ.
        fetches.append([payment("tl-garage-1"), bakery(), post_office(), payment("tl-garage-1")])
    return fetches


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


def standing(store: Store):
    movement = movement_completeness(store, canonical)
    return standing_of(effective_opening(store, CARD), [CARD], movement).own


@pytest.mark.parametrize("order", ORDERS, ids=["statements-first", "aggregator-first"])
@pytest.mark.parametrize(("rebuild", "again"), SHAPES, ids=SHAPE_IDS)
class TestAnItemListedTwiceUnderOneId:
    def test_Fault_WhenOneArtefactListsAnItemTwiceUnderOneId_ThereIsNone(
        self, made, order, rebuild, again
    ):
        store = made(
            order,
            payments_printed=1,
            fetches=listed_twice(again=again),
            rebuild=rebuild,
        )

        report = check_rows(store, canonical)

        assert report.row_faults == []
        assert report.rows_listed == report.rows_held

    def test_Report_WhenOneArtefactListsAnItemTwice_SaysSoOnceAsInformation(
        self, made, order, rebuild, again
    ):
        store = made(
            order,
            payments_printed=1,
            fetches=listed_twice(again=again),
            rebuild=rebuild,
        )

        report = check_rows(store, canonical)

        assert [r.says() for r in report.repeated] == [SAID_ONCE]
        assert report.describe().count(SAID_ONCE) == 1
        assert report.faults == 0

    def test_Agreement_WhenTheOnlyThingAgainstTheAccountWasTheRepeat_IsNotHeldBack(
        self, made, order, rebuild, again
    ):
        store = made(
            order,
            payments_printed=1,
            fetches=listed_twice(again=again),
            rebuild=rebuild,
        )

        own = standing(store)

        assert own.state == AGREES
        assert own.through == SECOND_CLOSING
        assert own.held is None


@pytest.mark.parametrize("again", [False, True], ids=["once", "again"])
class TestTwoIdsOfOneSizeInOneArtefact:
    def landed(self, made, *, again):
        store = made(("statements", "aggregator"), payments_printed=1, fetches=[])
        land_aggregator(store, [payment("tl-garage-1"), payment("tl-garage-2")], derive=False)
        if again:
            land_aggregator(
                store, [payment("tl-garage-2"), payment("tl-garage-1"), bakery()], derive=False
            )
        return store

    def test_Fault_WhenOneArtefactListsTwoItemsWithIdsOfTheirOwn_BothAreListedRows(
        self, made, again
    ):
        store = self.landed(made, again=again)

        faults = [f for f in check_rows(store, canonical).row_faults if f.source == "truelayer"]

        assert [(f.listed, f.held) for f in faults if f.day.isoformat() == "2025-10-30"] == [
            (2, 0)
        ]
        assert check_rows(store, canonical).repeated == []

    def test_Agreement_WhenTwoItemsOfTheirOwnAreListedAndNotHeld_IsHeldBackFromTheDay(
        self, made, again
    ):
        store = self.landed(made, again=again)

        own = standing(store)

        assert own.state == HELD_MOVEMENT
        assert own.through is None
