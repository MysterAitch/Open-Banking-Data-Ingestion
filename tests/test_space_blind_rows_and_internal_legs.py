"""A row from a source that cannot see Spaces is never merged into an internal leg.

The deployed page showed the main account's side of a transfer to a Space, and a
round-up leg, as "seen by starling, starling-csv, truelayer": a Space-blind
source never lists a movement between the account and its own Space, so those
sightings belonged to a PAYMENT that the export and the aggregator file under
the one account. The matcher had merged the payment's row with the transfer leg
of the same amount a day earlier, and the Space's own payment row was then
counted with no export sighting ("a round-up leg with no pair; a Space arrival
of the same size lies within three days").

Every scenario varies the `round_up_corpus` household (a main account and one
Space, September 2026, amounts in pence), adds a Space-blind export and an
aggregator row, lands the artefacts in EVERY arrival order and rebuilds, so the
rule is proved on replayed data and not only on a live arrival.

KNOWN ANSWERS, worked by hand before the first run (the household alone has five
confirmed transfer pairs, one folded export row for the Hotel, and every stated
balance in agreement):

    (i) money moved main to the Space on day 4 (2000) and a payment of 2000
        made from the Space on day 5, the export and the aggregator each listing
        the payment under the main account
        the transfer leg is seen by the feed alone and pairs with the Space's arrival: six pairs
        the Space's payment row is seen by all three sources
        the export's and the aggregator's rows are one row (two blind sources
        reporting one payment), folded into the Space's payment row
        every stated balance agrees
    (ii) a card payment of 50 on day 3, the day Coffee's 50 round-up leaves
        the export's row for the small payment stays its own payment, folded into
        nothing: seen by the feed and the export
        the round-up leg is seen by the feed alone and pairs with its Space arrival: five pairs
        every stated balance agrees
    (iii) money moved Space to main on day 4 (2000) and a main-account payment of
        2000 on day 5
        the export's row merges with the main payment (seen by both) and nothing is folded
        for it
        every stated balance agrees
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterable
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from obdi.balance_anchors import effective_opening
from obdi.core.models import Transaction, TransactionStatus
from obdi.ingest.family_anchors import families_of
from obdi.ingest.matching import CandidateIndex, resolve
from obdi.ingest.pipeline import import_file
from obdi.ingest.providers import truelayer
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.ingest.store import Store
from round_up_corpus import (
    SPACE_FEED_ORIGIN,
    card_payment,
    land_feed,
    main_feed,
    space_arrival,
    space_feed,
)
from test_export_cuts import Row, export_lines
from test_family_anchors import FEED_ORIGIN, land_evidence
from test_matching import txn
from test_space_attribution import BILLS, MAIN, MAP

BASE_EXPORT = [
    Row("Deposit", 100000, 1, 1),
    Row("Coffee", -350, 3, 3),
    Row("Grocer", -2310, 5, 5),
    Row("Book", -1000, 5, 5),
    Row("Taxi", -1275, 8, 8),
    Row("Hotel", -3000, 10, 10),
    Row("Lunch", -800, 12, 12),
]

ARRIVALS = ("feed", "export", "aggregator")
ORDERS = list(itertools.permutations(ARRIVALS))


def order_id(order: tuple[str, ...]) -> str:
    return "-".join(order)


def transfer_to_the_space(uid: str, minor: int, day: int) -> dict[str, Any]:
    """The main account's side of money moved into the Space, as the feed reports it."""
    return card_payment(
        uid,
        "Savings",
        minor,
        day,
        source="INTERNAL_TRANSFER",
        counterPartyType="CATEGORY",
        counterPartyUid="cat-bills",
    )


def aggregator_record(ident: str, amount: str, day: int, who: str) -> dict[str, Any]:
    return {
        "transaction_id": f"volatile-{ident}",
        "normalised_provider_transaction_id": ident,
        "timestamp": f"2026-09-{day:02}T10:00:00Z",
        "description": who,
        "amount": amount,
        "currency": "GBP",
        "transaction_type": "DEBIT" if amount.startswith("-") else "CREDIT",
    }


def in_export_order(rows: Iterable[Row]) -> list[Row]:
    return sorted(rows, key=lambda row: row.export_day)


def corpus(
    directory,
    order: tuple[str, ...],
    *,
    main: list[dict[str, Any]],
    space: list[dict[str, Any]],
    export_rows: list[Row],
    aggregator: list[dict[str, Any]],
) -> Store:
    """The raw artefacts landed in `order` and rebuilt: nothing derived is trusted."""
    store = Store(directory / "household.sqlite3")
    land_evidence(store)
    path = directory / "export.csv"
    path.write_text("\n".join(export_lines(in_export_order(export_rows))) + "\n", encoding="utf-8")

    def feed() -> None:
        land_feed(store, main, origin=FEED_ORIGIN, asked="2026-09-02T00:00:00Z")
        land_feed(store, space, origin=SPACE_FEED_ORIGIN)

    def export() -> None:
        import_file(store, path, account_id=MAIN, account_map=MAP)

    def aggregate() -> None:
        store.land_artefact(
            truelayer.artefact_for(
                json.dumps({"results": aggregator}).encode(),
                account_id="tl-main",
                kind="booked",
            )
        )

    steps = {"feed": feed, "export": export, "aggregator": aggregate}
    for name in order:
        steps[name]()
    assert rebuild_from_raw(store, account_map=MAP).problems == []
    return store


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


def rows(store: Store, account: str, *, minor: int | None = None) -> list[Transaction]:
    return [
        t
        for t in store.transactions_for_account(account)
        if minor is None or t.amount_minor == minor
    ]


def sources(store: Store, row: Transaction) -> set[str]:
    return set(store.sources_for(row.entity_id))


def walk_differences(store: Store) -> dict[date, int]:
    opening = effective_opening(store, MAIN, families=families_of(store, MAP))
    assert opening.family is not None
    return {r.day: r.difference_minor for r in opening.family.readings if r.difference_minor}


def folded(store: Store, account: str, minor: int) -> list[Transaction]:
    return [t for t in rows(store, account, minor=minor) if t.status is TransactionStatus.FOLDED]


class TestMoneyMovedToASpaceAndPaidOutOfItTheNextDay:
    def scenario(self, stores, order):
        return stores(
            order,
            main=[
                *main_feed(),
                card_payment("f-tea", "Tea", 450, 4),
                transfer_to_the_space("f-xfer", 2000, 4),
            ],
            space=[
                *space_feed(),
                space_arrival("s-xfer", 2000, 4),
                card_payment("s-bill", "Bill", 2000, 5),
            ],
            export_rows=[*BASE_EXPORT, Row("Tea", -450, 4, 4), Row("Bill", -2000, 5, 5)],
            aggregator=[aggregator_record("tl-bill", "-20.00", 5, "BILL DD")],
        )

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_TransferLeg_WhenTheExportAndAggregatorListThePayment_IsSeenByTheFeedAlone(
        self, stores, order
    ):
        store = self.scenario(stores, order)

        (leg,) = [t for t in rows(store, MAIN, minor=-2000) if t.is_internal_transfer]
        assert sources(store, leg) == {"starling"}

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_TransferLeg_WhenThePaymentIsListedUnderMain_StillPairsWithTheSpacesArrival(
        self, stores, order
    ):
        store = self.scenario(stores, order)

        (leg,) = [t for t in rows(store, MAIN, minor=-2000) if t.is_internal_transfer]
        (arrival,) = list(rows(store, BILLS, minor=2000))
        assert (leg.entity_id, arrival.entity_id) in store.confirmed_transfer_pairs()
        assert len(store.confirmed_transfer_pairs()) == 6

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_SpacePayment_WhenTheBlindSourcesListIt_IsSeenByAllThree(self, stores, order):
        store = self.scenario(stores, order)

        (bill,) = list(rows(store, BILLS, minor=-2000))
        assert {"starling", "starling-csv", "truelayer"} <= sources(store, bill)

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_BlindRows_WhenTheyListThePayment_AreOneRowFoldedIntoTheSpacesRow(self, stores, order):
        store = self.scenario(stores, order)

        (copy,) = folded(store, MAIN, -2000)
        assert {"starling-csv", "truelayer"} <= sources(store, copy)

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_StatedBalances_WhenThePaymentFollowsTheTransfer_EveryOneAgrees(self, stores, order):
        store = self.scenario(stores, order)

        assert walk_differences(store) == {}


class TestASmallPaymentEqualToARoundUp:
    def scenario(self, stores, order):
        return stores(
            order,
            main=[*main_feed(), card_payment("f-sweet", "Sweet", 50, 3)],
            space=space_feed(),
            export_rows=[*BASE_EXPORT, Row("Sweet", -50, 3, 3)],
            aggregator=[aggregator_record("tl-sweet", "-0.50", 3, "SWEET")],
        )

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_RoundUpLeg_WhenAPaymentOfTheSameSizeIsListed_IsSeenByTheFeedAlone(
        self, stores, order
    ):
        store = self.scenario(stores, order)

        (leg,) = [t for t in rows(store, MAIN, minor=-50) if "roundUpOf" in t.raw]
        assert sources(store, leg) == {"starling"}

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_SmallPayment_WhenItsAmountEqualsARoundUp_StaysItsOwnPaymentSeenByAllThree(
        self, stores, order
    ):
        store = self.scenario(stores, order)

        (sweet,) = [t for t in rows(store, MAIN, minor=-50) if "roundUpOf" not in t.raw]
        assert sweet.status is TransactionStatus.BOOKED
        assert {"starling", "starling-csv", "truelayer"} <= sources(store, sweet)

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_RoundUpLeg_WhenASmallPaymentOfTheSameSizeIsListed_StillPairsWithItsSpaceArrival(
        self, stores, order
    ):
        store = self.scenario(stores, order)

        assert len(store.confirmed_transfer_pairs()) == 5
        assert walk_differences(store) == {}


class TestMoneyMovedBackFromASpaceThenPaidFromMain:
    def scenario(self, stores, order):
        return stores(
            order,
            main=[
                *main_feed(),
                space_arrival(
                    "f-back",
                    2000,
                    4,
                    counterPartyUid="cat-bills",
                    counterPartyName="Bills",
                    transactionTime="2026-09-04T10:00:02.000Z",
                ),
                card_payment("f-pay", "Plumber", 2000, 5),
            ],
            space=[*space_feed(), space_arrival("s-back", 2000, 4, direction="OUT")],
            export_rows=[*BASE_EXPORT, Row("Plumber", -2000, 5, 5)],
            aggregator=[aggregator_record("tl-pay", "-20.00", 5, "PLUMBER")],
        )

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_MainPayment_WhenTheSpaceHandedBackTheSameAmount_StillMergesWithTheBlindRows(
        self, stores, order
    ):
        store = self.scenario(stores, order)

        (plumber,) = list(rows(store, MAIN, minor=-2000))
        assert plumber.status is TransactionStatus.BOOKED
        assert {"starling", "starling-csv", "truelayer"} <= sources(store, plumber)

    @pytest.mark.parametrize("order", ORDERS, ids=order_id)
    def test_StatedBalances_WhenTheSpaceHandedMoneyBack_EveryOneAgrees(self, stores, order):
        store = self.scenario(stores, order)

        assert walk_differences(store) == {}
        assert len(store.confirmed_transfer_pairs()) == 6


class TestTheRuleOnItsOwn:
    """`resolve` with a blind-source predicate, without a store or a rebuild."""

    @staticmethod
    def leg(**kwargs: Any) -> Transaction:
        return replace(txn(source="starling", **kwargs), is_internal_transfer=True)

    @staticmethod
    def blind(source: str) -> bool:
        return source in {"starling-csv", "truelayer"}

    def test_BlindRow_WhenTheCandidateIsAnInternalLeg_IsNotMerged(self):
        index = CandidateIndex(
            [self.leg(entity_id="leg", source_id="f-1")], space_blind=self.blind
        )

        result = resolve(txn(source="starling-csv", day=15), index)

        assert result.existing is None
        assert result.near_misses == ()

    def test_InternalLeg_WhenTheCandidateIsABlindSourcesRow_IsNotMerged(self):
        index = CandidateIndex(
            [txn(source="truelayer", entity_id="tl", source_id="t-1")],
            space_blind=self.blind,
        )

        result = resolve(self.leg(source_id="f-1", day=15), index)

        assert result.existing is None
        assert result.near_misses == ()

    def test_BlindRow_WhenTheCandidateIsAnOrdinaryPayment_StillMerges(self):
        stored = txn(source="starling", entity_id="pay", source_id="f-1")
        index = CandidateIndex([stored], space_blind=self.blind)

        assert resolve(txn(source="starling-csv", day=15), index).existing == stored

    def test_BlindRow_WhenNoPredicateIsGiven_MergesAsItAlwaysDid(self):
        stored = self.leg(entity_id="leg", source_id="f-1")

        result = resolve(txn(source="starling-csv", day=15), CandidateIndex([stored]))

        assert result.existing == stored

    def test_InternalLeg_WhenBothSidesAreTheFeed_IsGovernedByTheOrdinaryRules(self):
        index = CandidateIndex(
            [self.leg(entity_id="leg", source_id="f-1")], space_blind=self.blind
        )

        result = resolve(self.leg(source_id="f-1"), index)

        assert result.existing is not None
        assert result.existing.entity_id == "leg"
