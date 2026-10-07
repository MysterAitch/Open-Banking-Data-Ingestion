"""An aggregator item that states a feed item's uid IS that payment, whatever the dates say.

Measured on one real month, 58 of 58 aggregator items carried a feed item's own uid and all
58 agreed with it on size and direction; the matcher read none of them.
Each scenario lands the sources live through the doors a pull and an import use, in every
order, with invented payloads read by the real providers and importers.
Rebuilds are used only where the answer does not depend on the order a rebuild replays in
(an id's does not, and an export's date rule can).

KNOWN ANSWERS, decided before the first run (pence):

    late settlement (six payments, two settled months later) with the aggregator stating uids
        every payment's aggregator sighting is on the row the feed's sighting is on,
        whichever source arrived first, and its basis is the id
    two equal payments minutes apart, the aggregator listing them in the other order
        two rows; each row's feed uid is the uid its aggregator sighting states
    an aggregator item whose uid names a feed item of ANOTHER size
        two rows, never merged, and one review flag naming the size
    an aggregator that states no uid
        behaves as before: joined by window or by settlement day, never by id
    a Space payment the aggregator reports under the main account, beside a main-account
    payment of the same size a day earlier
        the Space's row is the one the aggregator's item folds into; the main payment is
        sighted by the feed and the aggregator and stays counted
    a heuristic pairing an id contradicts (the aggregator item names a payment not yet
    landed, a feed item of the same size and day has)
        not made: the aggregator item has its own row, which the feed item then joins by id
    its own id and its stated uid naming different rows (planted)
        nothing further is merged and the row is flagged
    the same source listing the same thing again from another artefact (a second export that
    overlaps the first, a second aggregator fetch that lists the same items again)
        no row is added for anything already held; each payment keeps its three sightings
"""

from __future__ import annotations

import itertools
import json
import pathlib
from dataclasses import replace
from datetime import date

import pytest

from late_settlement_corpus import (
    HOUSEHOLD_EXPORT,
    Payment,
    aggregator_item,
    export_text,
    household,
    late_settlement_payments,
)
from obdi.core.models import RawArtefact
from obdi.ingest.family_anchors import families_of
from obdi.ingest.pipeline import import_file, reconcile_batch
from obdi.ingest.providers import starling, truelayer
from obdi.ingest.rebuild import parse_artefact_transactions
from obdi.ingest.space_attribution import fold_space_copies, plan_folds, space_parents
from obdi.ingest.store import Store
from round_up_corpus import SPACE_FEED_ORIGIN, card_payment
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_space_attribution import BILLS, MAIN, MAP
from test_space_blind_rows_and_internal_legs import order_id, sources

ARRIVALS = ("feed", "export", "aggregator")
ORDERS = list(itertools.permutations(ARRIVALS))
FEED_AGGREGATOR_EXPORT = ("feed", "aggregator", "export")


@pytest.fixture
def made(tmp_path):
    opened: list[Store] = []

    def build(order, payments, **kwargs) -> Store:
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        opened.append(household(directory, order, payments, linked=True, **kwargs))
        return opened[-1]

    yield build
    for store in opened:
        store.close()


def rows_of(store: Store, minors: set[int], account: str = MAIN):
    return [
        t
        for t in store.transactions_for_account(account)
        if abs(t.amount_minor) in minors
        and not t.status.is_history
        and not (t.source_id or "").endswith(":round-up")
        and t.description != "Round-up"
        and t.is_internal_transfer is False
    ]


def feed_uids(store: Store, row) -> set[str]:
    return store.stated_ids_by_entity()[1].get(row.entity_id, set())


def stated_links(store: Store, row) -> set[str]:
    return store.stated_ids_by_entity()[0].get(row.entity_id, set())


def rows_of_payment(store: Store, payment: Payment):
    """The payment's rows: those the feed sighted under its uid."""
    return [r for r in rows_of(store, {payment.minor}) if payment.uid in feed_uids(store, r)]


def bases(store: Store, row, source: str, account: str = MAIN) -> set[str]:
    return {b for s, b, _ in store.bases_by_entity(account).get(row.entity_id, []) if s == source}


class TestLateSettlementHousehold:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Payments_WhenTheAggregatorStatesTheFeedsUid_ShareARowInEveryOrder(self, made, order):
        payments = late_settlement_payments()
        store = made(order, payments)

        for payment in payments:
            rows = rows_of_payment(store, payment)
            assert len(rows) == 1
            assert payment.uid in stated_links(store, rows[0])

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_AggregatorSightings_WhenJoinedToTheFeedsRow_AreRecordedAsJoinedByTheId(
        self, made, order
    ):
        payments = late_settlement_payments()
        store = made(order, payments)

        for payment in payments:
            (row,) = rows_of_payment(store, payment)
            joined = bases(store, row, "truelayer") | bases(store, row, "starling")
            assert "id" in joined

    def test_Payments_WhenTheStoreIsRebuiltFromRaw_StillShareARowAndTheBasisIsRebuilt(
        self, made
    ):
        payments = late_settlement_payments()
        store = made(FEED_AGGREGATOR_EXPORT, payments, rebuild=True)

        for payment in payments:
            (row,) = rows_of_payment(store, payment)
            assert "id" in bases(store, row, "truelayer") | bases(store, row, "starling")
            assert len(rows_of(store, {payment.minor})) == 1


GARAGE_A = Payment(
    "f-garage-a", "Garage", 2000, "2026-09-14T10:00:00.000Z", None, None, 14
)
GARAGE_B = Payment(
    "f-garage-b", "Garage", 2000, "2026-09-14T10:05:00.000Z", None, None, 14
)


class TestEqualPaymentsMinutesApart:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Payments_WhenTheAggregatorListsThemInTheOtherOrder_EachItemFindsItsOwnPayment(
        self, tmp_path, order
    ):
        store = household(
            tmp_path, order, [GARAGE_A, GARAGE_B], linked=True, aggregator_reversed=True
        )
        try:
            rows = rows_of(store, {2000})
            assert len(rows) == 2
            for row in rows:
                (uid,) = feed_uids(store, row)
                assert stated_links(store, row) == {uid}
        finally:
            store.close()


class TestTheSizeOfAnIdsPayment:
    @pytest.mark.parametrize("order", [("feed", "aggregator"), ("aggregator", "feed")])
    def test_Item_WhenItsUidNamesAFeedItemOfAnotherSize_IsNeverMergedAndIsFlagged(
        self, tmp_path, order
    ):
        wrong = Payment(
            "f-wrong", "Garage", 3000, "2026-09-14T10:00:00.000Z", None, None, 14,
            {"amount": "-31.00"},
        )
        store = household(tmp_path, [*order, "export"], [wrong], linked=True)
        try:
            assert len(rows_of(store, {3000})) == 1
            assert len(rows_of(store, {3100})) == 1
            flagged = [r for r in store.review_queue() if "another size" in str(r["reason"])]
            assert len(flagged) == 1
        finally:
            store.close()


class TestNoIdsAtAll:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Aggregator_WhenItStatesNoUid_JoinsAsBeforeAndNeverByTheId(self, tmp_path, order):
        payments = late_settlement_payments()
        store = household(tmp_path, order, payments)
        try:
            everything = {b for rows in store.bases_by_entity(MAIN).values() for _, b, _ in rows}
            assert "id" not in everything
            assert {"window", "settlement"} & everything
        finally:
            store.close()


class TestASpacePayment:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Aggregator_WhenItsItemStatesASpaceUid_FoldsIntoThatSpacesRowNotTheMainLookalike(
        self, made, order
    ):
        payments = [
            Payment("f-decoy", "Decoy", 3000, "2026-09-09T10:00:00.000Z", None, None, 9),
            Payment("s-hotel", "Hotel", 3000, "2026-09-10T10:00:00.000Z", None, None, 10,
                    in_feed=False),
        ]
        store = made(order, payments)

        # The export names no id, so which of the two it lists is its own guess and is not
        # asserted; what the aggregator's ids decide is.
        main = rows_of(store, {3000})
        assert len(main) == 1
        assert {"starling", "truelayer"} <= sources(store, main[0])
        assert stated_links(store, main[0]) == {"f-decoy"}
        (space,) = rows_of(store, {3000}, BILLS)
        assert {"starling", "truelayer"} <= sources(store, space)
        folded = [
            t for t in store.transactions_for_account(MAIN)
            if abs(t.amount_minor) == 3000 and t.status.is_history
        ]
        assert len(folded) == 1
        assert "id" in bases(store, space, "truelayer", BILLS)


def arrive(store: Store, artefact: RawArtefact, account_ref: str) -> None:
    store.land_artefact(artefact)
    reconcile_batch(
        store,
        parse_artefact_transactions(
            artefact.source, artefact.payload, account_ref, artefact.digest
        ),
        digest=artefact.digest,
        space_blind=families_of(store, MAP).blind_in,
    )
    fold_space_copies(store, MAP)


def feed_artefact(items):
    return starling.artefact_for(
        json.dumps({"feedItems": items}).encode(),
        account_id="starling:cat-main",
        kind="feed",
        origin=f"{FEED_ORIGIN}?changesSince=2026-09-02T00:00:00Z",
    )


def aggregator_artefact(items):
    return truelayer.artefact_for(
        json.dumps({"results": items}).encode(), account_id="tl-main", kind="booked"
    )


A = Payment("f-a", "Garage", 2000, "2026-09-14T10:00:00.000Z", None, None, 14)
B = Payment("f-b", "Garage", 2000, "2026-09-14T10:05:00.000Z", None, None, 14)


@pytest.fixture
def bare(tmp_path):
    store = Store(tmp_path / "bare.sqlite3")
    land_evidence(store)
    yield store
    store.close()


class TestOneOfTwoEqualBillsInASpace:
    def test_Fold_WhenTheCopyStatesWhichBillItIs_FoldsIntoThatOneWhereAmountAndDateCannot(
        self, bare
    ):
        bills = [
            card_payment(
                "s-bill-1", "Bills Co", 4500, 10, transactionTime="2026-09-10T10:00:00.000Z"
            ),
            card_payment(
                "s-bill-2", "Bills Co", 4500, 10, transactionTime="2026-09-10T10:05:00.000Z"
            ),
        ]
        arrive(
            bare,
            starling.artefact_for(
                json.dumps({"feedItems": bills}).encode(),
                account_id="starling:cat-bills",
                kind="feed",
                origin=f"{SPACE_FEED_ORIGIN}?changesSince=2026-09-01T00:00:00Z",
            ),
            BILLS,
        )
        copy = Payment(
            "s-bill-2", "Bills Co", 4500, "2026-09-10T10:05:00.000Z", None, None, 10, in_feed=False
        )
        arrive(bare, aggregator_artefact([aggregator_item(copy, link=True)]), MAIN)

        (main_copy,) = [t for t in bare.transactions_for_account(MAIN) if t.amount_minor == -4500]
        assert main_copy.status.name == "FOLDED"
        space_rows = rows_of(bare, {4500}, BILLS)
        assert len(space_rows) == 2
        copied = {
            row.source_id: {s for s, _, _ in bare.bases_by_entity(BILLS)[row.entity_id]}
            for row in space_rows
        }
        assert copied["s-bill-2"] >= {"truelayer"}
        assert "truelayer" not in copied["s-bill-1"]
        assert "id" in bases(bare, next(r for r in space_rows if r.source_id == "s-bill-2"),
                             "truelayer", BILLS)

        # The same rows with the ids withheld: the rule that stays as the fallback cannot say
        # which bill it copies, so it folds nothing.
        feeds = {s: {str(a) for a in accounts} for s, accounts in MAP.accounts_by_source().items()}
        without = plan_folds(
            bare.all_transactions(),
            bare.genuine_sightings(),
            feeds,
            space_parents(bare, MAP),
        )
        assert without.folds == {}
        assert [r.reason for r in without.refusals] == ["more-space-rows"]


class TestAHeuristicAnIdContradicts:
    @pytest.mark.parametrize(
        "order", list(itertools.permutations(("feed-a", "aggregator-b", "feed-b"))), ids=order_id
    )
    def test_Item_WhenItNamesAPaymentNotYetLandedAndAnEqualOneIsHeld_IsNotMergedIntoTheWrongOne(
        self, bare, order
    ):
        steps = {
            "feed-a": lambda: arrive(bare, feed_artefact([A.feed_item()]), MAIN),
            "feed-b": lambda: arrive(bare, feed_artefact([B.feed_item()]), MAIN),
            "aggregator-b": lambda: arrive(
                bare, aggregator_artefact([aggregator_item(B, link=True)]), MAIN
            ),
        }
        for name in order:
            steps[name]()

        rows = rows_of(bare, {2000})
        assert len(rows) == 2
        by_uid = {next(iter(feed_uids(bare, r))): r for r in rows}
        assert set(by_uid) == {"f-a", "f-b"}
        assert stated_links(bare, by_uid["f-a"]) == set()
        assert stated_links(bare, by_uid["f-b"]) == {"f-b"}


class TestTwoIdsNamingDifferentRows:
    def test_Item_WhenItsOwnIdAndItsUidNameDifferentRows_NothingMoreIsMergedAndTheRowIsFlagged(
        self, bare
    ):
        feed = feed_artefact([A.feed_item(), B.feed_item()])
        aggregator = aggregator_artefact([aggregator_item(B, link=True)])
        bare.land_artefact(feed)
        bare.land_artefact(aggregator)
        feed_rows = {
            t.source_id: t
            for t in parse_artefact_transactions(feed.source, feed.payload, MAIN, feed.digest)
        }
        (item,) = parse_artefact_transactions(
            aggregator.source, aggregator.payload, MAIN, aggregator.digest
        )
        # Planted: the item's sighting sits on the OTHER equal payment's row, as an old
        # matcher would have left it, so its own id names row A while its stated uid names B.
        for uid in ("f-a", "f-b"):
            placed = replace(feed_rows[uid], entity_id=f"e-{uid}")
            bare.upsert_transaction(placed, match_tier="unresolved")
            bare.record_source(placed)
        bare.record_source(replace(item, entity_id="e-f-a"))
        bare.connection.commit()
        before = len(bare.all_transactions())

        arrive(bare, aggregator, MAIN)

        assert len(bare.all_transactions()) == before
        flagged = [r for r in bare.review_queue() if "different rows" in str(r["reason"])]
        assert [r["entity_id"] for r in flagged] == ["e-f-a"]


def second_export(directory: pathlib.Path, payments, extra) -> pathlib.Path:
    listed = [
        *HOUSEHOLD_EXPORT,
        *((p.name, -p.minor, p.listed) for p in payments if p.listed is not None),
        *extra,
    ]
    path = directory / "second-export.csv"
    path.write_text(export_text(listed), encoding="utf-8")
    return path


class TestTheSameSourceListingTheSameThingAgain:
    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Rows_WhenASecondExportOverlapsTheFirst_NoPaymentIsHeldTwice(
        self, made, tmp_path, order
    ):
        payments = late_settlement_payments()
        store = made(order, payments)
        before = {p.uid: len(rows_of(store, {p.minor})) for p in payments}

        import_file(
            store,
            second_export(tmp_path, payments, (("Newsagent", -150, date(2026, 9, 20)),)),
            account_id=MAIN,
            account_map=MAP,
        )

        after = {p.uid: len(rows_of(store, {p.minor})) for p in payments}
        assert after == before
        assert len(rows_of(store, {150})) == 1

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_Rows_WhenASecondAggregatorFetchListsTheSameItems_NoPaymentIsHeldTwice(
        self, made, order
    ):
        payments = late_settlement_payments()
        store = made(order, payments)
        before = len(store.all_transactions())

        arrive(
            store,
            aggregator_artefact(
                [aggregator_item(p, link=True) for p in reversed(payments)]
                + [
                    aggregator_item(
                        Payment("f-extra", "Newsagent", 150, "2026-09-20T10:00:00.000Z", None,
                                None, 20),
                        link=True,
                    )
                ]
            ),
            MAIN,
        )

        assert len(store.all_transactions()) == before + 1
        for payment in payments:
            (row,) = rows_of_payment(store, payment)
            assert len(feed_uids(store, row)) == 1


class TestTheAggregatorArrivingAfterTheExport:
    def test_Aggregator_WhenItArrivesAfterTheExportJoinedTheFeedsRow_JoinsItByIdWhateverItsDate(
        self, made
    ):
        payments = late_settlement_payments()
        store = made(("feed", "export", "aggregator"), payments)

        late = [p for p in payments if p.minor in (4210, 1780)]
        for payment in late:
            rows = rows_of(store, {payment.minor})
            assert len(rows) == 1
            assert {"starling", "starling-csv", "truelayer"} <= sources(store, rows[0])
