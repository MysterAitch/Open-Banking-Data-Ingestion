"""A copy of a payment folds into a payment, and a fold that is refused says which refusal.

The deployed page showed, for a bill paid from the Bills Space, "out row dated
starling-csv 2022-10-28, truelayer 2022-10-28; seen by starling-csv, truelayer;
booked; in the main account" counted beside "out row dated starling 2022-10-28;
seen by starling; booked; in the Space starling-space-bills", and every rebuild
said "1 more could not be paired one to one and stay counted in the main
account" without saying why. The cause measured here: the Space's candidate rows
for the copy included its transfer legs of the same size, so one copy had two
choices and the group was left alone. A copy of a PAYMENT is a copy of a payment
row, and the sources that cannot see Spaces list no movement between the account
and a Space.

Every scenario is the `round_up_corpus` household (a main account and the Bills
Space, September 2026, amounts in pence), with the three sources landed in EVERY
order and rebuilt. Known answers, worked by hand before the first run:

    (i) a Space-to-main transfer of 2000 and a card payment of 2000 from the Space,
        both on day 4, the export and the aggregator listing the payment under main
        the main copy folds into the Space's PAYMENT: one folded row seen by
        the export and the aggregator, the payment seen by all three
        the rebuild reports no refusal, and every stated balance agrees
    (ii) a Bills payment of 2000 on day 5 and the Holiday Space's own OUT leg of
        2000 on day 5, the export and the aggregator listing the payment
        the copy still folds into the Bills payment
        (the Holiday leg has no main-side arrival, so the family really is over by it:
        no stated-balance claim is made)
    (iii) two identical card payments of 2000 from the Space on day 5, one aggregator
        row for them and nothing in the export
        nothing folds: 1 copy, 2 Space rows, the first refusal
        the rebuild summary and the page name the row's day and account and say so
    (iv) one card payment of 2000 from the Space on day 5, the aggregator reporting
        it twice under two ids
        nothing folds: 2 copies, 1 Space row, the second refusal
    (v) three copies and three Space rows of one size in a chain where two copies
        can only reach the same Space row: the third refusal, said of each copy
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.space_attribution import (
    MORE_COPIES,
    MORE_SPACE_ROWS,
    NO_ONE_TO_ONE,
    FoldRefusal,
    plan_folds,
)
from obdi.ingest.store import Store
from round_up_corpus import (
    SPACE_FEED_ORIGIN,
    card_payment,
    land_feed,
    main_feed,
    space_arrival,
    space_feed,
)
from test_export_cuts import Row
from test_export_dating import render
from test_internal_leg_pairing import CASES, SPACE_ORDERS, transfer_household
from test_space_attribution import (
    AGGREGATOR,
    BILLS,
    FEED,
    FEEDS,
    MAIN,
    MAP,
    planned_row,
)
from test_space_blind_rows_and_internal_legs import (
    BASE_EXPORT,
    ORDERS,
    aggregator_record,
    corpus,
    folded,
    order_id,
    rows,
    sources,
    walk_differences,
)


@pytest.fixture
def stores(tmp_path):
    opened: list[Store] = []

    def build(order, **kwargs) -> Store:
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        store = corpus(directory, order, **kwargs)
        opened.append(store)
        return store

    yield build
    for store in opened:
        store.close()


def second_bill(uid: str = "s-bill-2") -> dict:
    return card_payment(uid, "Bill", 2000, 5)


class TestACopyOfAPaymentBesideATransferLegOfTheSameSize:
    @pytest.mark.parametrize(("order", "space"), CASES)
    def test_Copy_WhenTheSpaceAlsoHandedBackTheSameAmount_FoldsIntoThePaymentRow(
        self, stores, order, space
    ):
        store = transfer_household(stores, order, SPACE_ORDERS[space]())

        (copy,) = folded(store, MAIN, -2000)
        assert {"starling-csv", "truelayer"} <= sources(store, copy)
        (payment,) = [t for t in rows(store, BILLS, minor=-2000) if t.source_id == "s-bill"]
        assert {"starling", "starling-csv", "truelayer"} <= sources(store, payment)

    @pytest.mark.parametrize(("order", "space"), CASES)
    def test_StatedBalances_WhenTheSpaceHandedBackAndPaidTheSameAmount_EveryOneAgrees(
        self, stores, order, space
    ):
        store = transfer_household(stores, order, SPACE_ORDERS[space]())

        assert walk_differences(store) == {}

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Page_WhenTheCopyFolds_NamesNoRefusal(self, stores, order):
        store = transfer_household(stores, order, SPACE_ORDERS["leg-first"]())

        assert "not folded into a Space row" not in render(store)


class TestACopyBesideAnotherSpacesLegOfTheSameSize:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Copy_WhenAnotherSpacesLegIsOfTheSameSize_StillFoldsIntoThePayment(
        self, stores, order
    ):
        store = stores(
            order,
            main=main_feed(),
            space=[*space_feed(), card_payment("s-bill", "Bill", 2000, 5)],
            export_rows=[*BASE_EXPORT, Row("Bill", -2000, 5, 5)],
            aggregator=[aggregator_record("tl-bill", "-20.00", 5, "BILL DD")],
        )
        land_feed(
            store,
            [space_arrival("h-out", 2000, 5, direction="OUT")],
            origin=SPACE_FEED_ORIGIN.replace("cat-bills", "cat-holiday"),
        )
        assert rebuild_from_raw(store, account_map=MAP).problems == []

        (copy,) = folded(store, MAIN, -2000)
        assert {"starling-csv", "truelayer"} <= sources(store, copy)
        (payment,) = [t for t in rows(store, BILLS, minor=-2000) if t.source_id == "s-bill"]
        assert {"starling-csv", "truelayer"} <= sources(store, payment)


def ambiguous_household(stores, order):
    """Two identical Space payments and one aggregator row for them; the export lists neither."""
    return stores(
        order,
        main=main_feed(),
        space=[*space_feed(), card_payment("s-bill-1", "Bill", 2000, 5), second_bill()],
        export_rows=BASE_EXPORT,
        aggregator=[aggregator_record("tl-bill", "-20.00", 5, "BILL DD")],
    )


class TestACopyOfOneOfTwoIdenticalPayments:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Copy_WhenTwoIdenticalSpacePaymentsLieNearIt_IsLeftCountedAndSaysWhy(
        self, stores, order
    ):
        store = ambiguous_household(stores, order)

        assert folded(store, MAIN, -2000) == []
        report = rebuild_from_raw(store, account_map=MAP)
        assert report.space_ambiguous == 1
        (refusal,) = report.space_refusals
        assert (refusal.account, refusal.day, refusal.reason) == (
            MAIN,
            date(2026, 9, 5),
            MORE_SPACE_ROWS,
        )
        assert (refusal.copies, refusal.space_rows) == (1, 2)

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_RebuildSummary_WhenACopyIsRefused_NamesItsAccountDayAndReason(self, stores, order):
        store = ambiguous_household(stores, order)

        summary = rebuild_from_raw(store, account_map=MAP).describe()

        assert (
            "not folded: the starling-personal row dated 2026-09-05 - 2 Space rows of its "
            "size lie near 1 copy, so which one it copies cannot be told."
        ) in summary

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Page_WhenACopyIsRefused_SaysWhichRefusalItWas(self, stores, order):
        page = render(ambiguous_household(stores, order))

        assert (
            "not folded into a Space row: 2 Space rows of its size lie near 1 copy, so "
            "which one it copies cannot be told"
        ) in page


class TestTwoCopiesOfOnePayment:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Copies_WhenTheAggregatorReportsOnePaymentTwice_AreLeftCountedAndSayWhy(
        self, stores, order
    ):
        store = stores(
            order,
            main=main_feed(),
            space=[*space_feed(), card_payment("s-bill", "Bill", 2000, 5)],
            export_rows=BASE_EXPORT,
            aggregator=[
                aggregator_record("tl-bill-a", "-20.00", 5, "BILL DD"),
                aggregator_record("tl-bill-b", "-20.00", 5, "BILL DD"),
            ],
        )

        assert folded(store, MAIN, -2000) == []
        report = rebuild_from_raw(store, account_map=MAP)
        assert [r.reason for r in report.space_refusals] == [MORE_COPIES, MORE_COPIES]
        assert report.space_refusals[0].describe() == (
            "2 copies lie near 1 Space row of its size, so one copy would have no Space "
            "row of its own"
        )


class TestTheFoldOnItsOwn:
    """`plan_folds` over invented rows, without a store."""

    DAY = date(2026, 9, 10)

    def test_Plan_WhenACopyHasATransferLegAndAPaymentNear_FoldsIntoThePayment(self):
        from dataclasses import replace

        rows_ = [
            planned_row("payment", BILLS, FEED, -500, self.DAY),
            replace(planned_row("leg", BILLS, FEED, -500, self.DAY), is_internal_transfer=True),
            planned_row("copy", MAIN, AGGREGATOR, -500, self.DAY),
        ]

        plan = plan_folds(rows_, {}, FEEDS, {BILLS: MAIN})

        assert plan.folds == {"copy": "payment"}
        assert plan.refusals == ()

    def test_Plan_WhenOnlyATransferLegIsNear_LeavesTheCopyUnmatched(self):
        from dataclasses import replace

        rows_ = [
            replace(planned_row("leg", BILLS, FEED, -500, self.DAY), is_internal_transfer=True),
            planned_row("copy", MAIN, AGGREGATOR, -500, self.DAY),
        ]

        plan = plan_folds(rows_, {}, FEEDS, {BILLS: MAIN})

        assert (plan.folds, plan.ambiguous, plan.unmatched) == ({}, 0, 1)

    def test_Plan_WhenTwoCopiesCanOnlyReachOneSpaceRow_SaysNoOneToOneOfEach(self):
        def day(offset: int) -> date:
            return date(2026, 9, offset)

        rows_ = [
            planned_row("s1", BILLS, FEED, -500, day(10)),
            planned_row("s2", BILLS, FEED, -500, day(12)),
            planned_row("s3", BILLS, FEED, -500, day(13)),
            planned_row("a", MAIN, AGGREGATOR, -500, day(8)),
            planned_row("b", MAIN, AGGREGATOR, -500, day(8)),
            planned_row("c", MAIN, AGGREGATOR, -500, day(12)),
        ]

        plan = plan_folds(rows_, {}, FEEDS, {BILLS: MAIN})

        assert plan.folds == {}
        assert [(r.entity_id, r.reason, r.copies, r.space_rows) for r in plan.refusals] == [
            ("a", NO_ONE_TO_ONE, 3, 3),
            ("b", NO_ONE_TO_ONE, 3, 3),
            ("c", NO_ONE_TO_ONE, 3, 3),
        ]

    def test_Refusal_WhenDescribed_NamesNoFigureAndNoPayee(self):
        refusal = FoldRefusal("copy", MAIN, self.DAY, NO_ONE_TO_ONE, 3, 3)

        assert refusal.describe() == (
            "3 copies and 3 Space rows lie near one another, but some copies can only "
            "be paired with the same Space row"
        )

    def test_Plan_WhenEveryCopyHasItsOwnSpaceRow_RefusesNone(self):
        rows_ = [
            planned_row("s1", BILLS, FEED, -500, self.DAY),
            planned_row("m1", MAIN, AGGREGATOR, -500, self.DAY),
        ]

        assert plan_folds(rows_, {}, FEEDS, {BILLS: MAIN}).refusals == ()
