"""An export row dated days after the feed's row is that row, even as one of two identical rows.

The deployed page showed a change on one day and an opposite change three days
later, each explained as a payment the export dates later than the feed does:
"out row dated starling 2021-03-22, truelayer 2021-03-22; seen by starling,
truelayer; booked; in the main account" counted and not listed by the export, and
"out row dated starling-csv 2021-03-25; seen by starling-csv; not held; not held
at all", with "The export lists 5 rows in the window and the store holds 4
sightings of it there: identical rows collapsed into one". The reading tried
first was that the export's second identical row, three days after the feed's,
lay beyond the matcher's window and found no partner or collapsed into its twin.

THE MEASUREMENT. It does not: three days is inside the matcher's window
(`matching.FUZZY_WINDOW_DAYS`), and in every arrival order of the three sources
the second identical row merges with the feed's earlier row, so the store holds a
sighting for each of the export's rows and no stated balance disagrees. Nothing
here was made wider, because nothing here needed it. What differs in the deployed
store is not known from the page's facts, and the ledger now says the facts that
decide it: whether the export lists a row of the same size within thirty days,
and what that row is sighted on
(`test_export_cuts.TestWhatTheOtherSideHoldsThatIsLikeAMissingRow`).

What these scenarios pin is the rule that a Space-blind export row with no partner
inside the window stays its own row, so the window is not stretched by a row
that merely looks like another.

Every scenario is the `round_up_corpus` household (September 2026, amounts in
pence) with the three sources landed in EVERY order and rebuilt. Known answers:

    (i) the feed holds a payment of 2000 on day 5 and another on day 8, the
        aggregator the first, and the export lists two identical rows on day 8
        two rows, each seen by the feed and the export, the first by the
        aggregator too; every stated balance agrees
    (ii) the same without the feed's payment on day 8
        two rows: the earlier payment merged with one export row and the other
        export row its own, seen by the export alone; every stated balance agrees
    (iii) the export dates the payment on day 14, nine days after the feed's
        two rows: the feed's payment and the export's, kept apart because the
        window is seven days; the family is over by the payment from day 14 on
"""

from __future__ import annotations

import pathlib

import pytest

from obdi.store import Store
from round_up_corpus import card_payment, main_feed, space_feed
from test_export_cuts import Row
from test_space_attribution import MAIN
from test_space_blind_rows_and_internal_legs import (
    BASE_EXPORT,
    ORDERS,
    aggregator_record,
    corpus,
    order_id,
    rows,
    sources,
    walk_differences,
)

AMOUNT = 2000


@pytest.fixture
def stores(tmp_path):
    opened: list[Store] = []

    def build(order, **kwargs) -> Store:
        directory: pathlib.Path = tmp_path / f"{len(opened)}"
        directory.mkdir()
        store = corpus(directory, order, **kwargs)
        opened.append(store)
        return store

    yield build
    for store in opened:
        store.close()


def held(store: Store) -> list[tuple[str, ...]]:
    return sorted(tuple(sorted(sources(store, t))) for t in rows(store, MAIN, minor=-AMOUNT))


class TestTwoIdenticalExportRowsThreeDaysAfterTheFeed:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_ExportRows_WhenTheFeedHoldsBothPayments_EachMergesWithItsOwnFeedRow(
        self, stores, order
    ):
        store = stores(
            order,
            main=[
                *main_feed(),
                card_payment("f-p1", "Bill", AMOUNT, 5),
                card_payment("f-p2", "Bill", AMOUNT, 8),
            ],
            space=space_feed(),
            export_rows=[*BASE_EXPORT, Row("Bill", -AMOUNT, 5, 8), Row("Bill", -AMOUNT, 8, 8)],
            aggregator=[aggregator_record("tl-p1", "-20.00", 5, "BILL DD")],
        )

        assert held(store) == [
            ("starling", "starling-csv"),
            ("starling", "starling-csv", "truelayer"),
        ]
        assert walk_differences(store) == {}

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_ExportRows_WhenTheFeedHoldsOnlyTheEarlierPayment_TheOtherRowStaysItsOwn(
        self, stores, order
    ):
        store = stores(
            order,
            main=[*main_feed(), card_payment("f-p1", "Bill", AMOUNT, 5)],
            space=space_feed(),
            export_rows=[*BASE_EXPORT, Row("Bill", -AMOUNT, 5, 8), Row("Bill", -AMOUNT, 8, 8)],
            aggregator=[aggregator_record("tl-p1", "-20.00", 5, "BILL DD")],
        )

        assert held(store) == [
            ("starling", "starling-csv", "truelayer"),
            ("starling-csv",),
        ]
        assert walk_differences(store) == {}


class TestAnExportRowBeyondTheWindow:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_ExportRow_WhenDatedNineDaysAfterTheFeed_IsNotMergedAndTheFamilyIsOverByIt(
        self, stores, order
    ):
        store = stores(
            order,
            main=[*main_feed(), card_payment("f-p1", "Bill", AMOUNT, 5)],
            space=space_feed(),
            export_rows=[*BASE_EXPORT, Row("Bill", -AMOUNT, 5, 14)],
            aggregator=[aggregator_record("tl-p1", "-20.00", 5, "BILL DD")],
        )

        assert held(store) == [("starling", "truelayer"), ("starling-csv",)]
        assert set(walk_differences(store).values()) == {AMOUNT}
