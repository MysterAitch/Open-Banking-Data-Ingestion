"""A transfer leg pairs with the transfer leg in the Space it names, never with a payment.

The deployed page showed a transfer from the Bills Space to the main account as
"in row ... a transfer leg, confirmed paired with the Space starling-space-bills"
beside "out row ... booked, a transfer leg with no pair; in the Space
starling-space-bills" and "out row ... booked; in the Space starling-space-bills",
an ordinary payment of the same size. The main account's IN leg had been paired
with the Space's PAYMENT, so the Space's real leg had nothing to pair with and
the payment's Space-blind copies were counted beside it.

Every scenario is the `round_up_corpus` household with one Space-to-main transfer
and one card payment from the same Space, of one amount, on one day, the export
and the aggregator listing the payment under the main account. The Space's two
rows are fed in BOTH orders, and the three sources land in EVERY order, then
rebuild, so pairing is proved on replayed data whichever row the store holds first.

KNOWN ANSWERS, worked by hand before the first run (the household alone has five
confirmed transfer pairs):

    (i) a Space-to-main transfer of 2000 on day 4 and a card payment of 2000
        from the Space on day 4
        the main account's IN leg pairs with the Space's OUT leg: six pairs
        the Space's payment pairs with nothing
        (the payment's folded copies and the stated balances are
        `test_space_fold_refusals`)
    (ii) the same, and a second Space (Holiday) with its own OUT leg of 2000 on day 4,
        the main account's IN leg naming Bills
        the main IN leg pairs with the Bills leg, never the Holiday leg
        the Holiday leg is left unpaired
    (iii) a leg whose Space is not mapped (an unknown category uid)
        it is paired as it always was: with a row of the opposite sign and size
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from obdi.matching import pair_transfer_entities
from obdi.models import Transaction
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store
from round_up_corpus import (
    SPACE_FEED_ORIGIN,
    card_payment,
    land_feed,
    main_feed,
    space_arrival,
    space_feed,
)
from test_export_cuts import Row
from test_matching import txn
from test_space_attribution import BILLS, HOLIDAY, MAIN, MAP
from test_space_blind_rows_and_internal_legs import (
    BASE_EXPORT,
    ORDERS,
    aggregator_record,
    corpus,
    order_id,
)

AMOUNT = 2000
DAY = 4


def main_receipt(uid: str = "f-back") -> dict:
    """The main account's IN leg of money handed back by the Bills Space."""
    return space_arrival(
        uid,
        AMOUNT,
        DAY,
        counterPartyUid="cat-bills",
        counterPartyName="Bills",
        transactionTime=f"2026-09-{DAY:02}T10:00:02.000Z",
    )


def bills_leg() -> dict:
    return space_arrival("s-back", AMOUNT, DAY, direction="OUT")


def bills_payment() -> dict:
    return card_payment("s-bill", "Bill", AMOUNT, DAY)


SPACE_ORDERS = {
    "leg-first": lambda: [bills_leg(), bills_payment()],
    "payment-first": lambda: [bills_payment(), bills_leg()],
}


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


def transfer_household(stores, order, space_items):
    return stores(
        order,
        main=[*main_feed(), main_receipt()],
        space=[*space_feed(), *space_items],
        export_rows=[*BASE_EXPORT, Row("Bill", -AMOUNT, DAY, DAY)],
        aggregator=[aggregator_record("tl-bill", "-20.00", DAY, "BILL DD")],
    )


def row_of(store: Store, account: str, uid: str) -> Transaction:
    (found,) = [t for t in store.transactions_for_account(account) if t.source_id == uid]
    return found


CASES = [
    pytest.param(order, space, id=f"{order_id(order)}-{space}")
    for order in ORDERS
    for space in SPACE_ORDERS
]


class TestASpaceToMainTransferBesideAPaymentOfTheSameSize:
    @pytest.mark.parametrize(("order", "space"), CASES)
    def test_MainInLeg_WhenTheSpacePaidTheSameAmountThatDay_PairsWithTheSpacesLegNotItsPayment(
        self, stores, order, space
    ):
        store = transfer_household(stores, order, SPACE_ORDERS[space]())

        pairs = set(store.confirmed_transfer_pairs())
        receipt = row_of(store, MAIN, "f-back")
        leg = row_of(store, BILLS, "s-back")
        payment = row_of(store, BILLS, "s-bill")
        assert (leg.entity_id, receipt.entity_id) in pairs
        assert payment.entity_id not in {entity for pair in pairs for entity in pair}
        assert len(pairs) == 6


HOLIDAY_FEED_ORIGIN = SPACE_FEED_ORIGIN.replace("cat-bills", "cat-holiday")


def two_spaces_household(stores, order):
    """The main account's IN leg names Holiday, and Bills (sorted first by name)
    also handed back the same amount that day."""
    store = stores(
        order,
        main=[*main_feed(), main_receipt_from_holiday()],
        space=[*space_feed(), bills_leg()],
        export_rows=BASE_EXPORT,
        aggregator=[],
    )
    land_feed(
        store,
        [space_arrival("h-back", AMOUNT, DAY, direction="OUT")],
        origin=HOLIDAY_FEED_ORIGIN,
    )
    assert rebuild_from_raw(store, account_map=MAP).problems == []
    return store


def main_receipt_from_holiday() -> dict:
    return space_arrival(
        "f-back-holiday",
        AMOUNT,
        DAY,
        counterPartyUid="cat-holiday",
        counterPartyName="Holiday",
        transactionTime=f"2026-09-{DAY:02}T10:00:02.000Z",
    )


class TestTwoSpacesHandingBackTheSameAmount:
    @pytest.mark.parametrize("order", ORDERS[:2], ids=order_id)
    def test_MainInLeg_WhenTwoSpacesHaveALegOfTheSameSize_PairsWithTheSpaceItNames(
        self, stores, order
    ):
        store = two_spaces_household(stores, order)

        receipt = row_of(store, MAIN, "f-back-holiday")
        holiday = row_of(store, HOLIDAY, "h-back")
        assert (holiday.entity_id, receipt.entity_id) in set(store.confirmed_transfer_pairs())

    @pytest.mark.parametrize("order", ORDERS[:2], ids=order_id)
    def test_SpaceLeg_WhenAnotherSpacesLegWasTheNamedPartner_IsLeftUnpaired(self, stores, order):
        store = two_spaces_household(stores, order)

        bills = row_of(store, BILLS, "s-back")
        paired = {entity for pair in store.confirmed_transfer_pairs() for entity in pair}
        assert bills.entity_id not in paired


def leg_of(entity: str, account: str, amount: int, names: str | None = None, **more):
    return replace(
        txn(entity_id=entity, account=account, amount=amount, **more),
        is_internal_transfer=True,
        raw={"counterPartyUid": names} if names else {},
    )


NAMED = {"cat-bills": BILLS, "cat-main": MAIN}


class TestPairingOnItsOwn:
    """`pair_transfer_entities` over invented rows, with the category resolved by a stand-in."""

    @staticmethod
    def named(row: Transaction) -> str | None:
        return NAMED.get(str(row.raw.get("counterPartyUid", "")))

    def pairs(self, rows: list[Transaction], *, known: bool = True) -> set[tuple[str, str]]:
        return set(pair_transfer_entities(rows, counterpart=self.named if known else None))

    def test_Leg_WhenAPaymentComesFirstInTheOrderOfChoice_IsStillGivenALegNotThePayment(self):
        payment = txn(entity_id="payment", account=BILLS, amount=-2000)
        leg = leg_of("leg", BILLS, -2000, "cat-main")
        receipt = leg_of("receipt", MAIN, 2000, "cat-bills")

        assert self.pairs([payment, leg, receipt]) == {("leg", "receipt")}

    def test_Leg_WhenItsNamedSpaceHoldsOnlyAPayment_IsLeftUnpaired(self):
        payment = txn(entity_id="payment", account=BILLS, amount=-2000)
        receipt = leg_of("receipt", MAIN, 2000, "cat-bills")

        assert self.pairs([payment, receipt]) == set()

    def test_Leg_WhenTheCounterpartIsNotKnown_PairsWithAnOrdinaryRowAsItAlwaysDid(self):
        payment = txn(entity_id="payment", account=BILLS, amount=-2000)
        receipt = leg_of("receipt", MAIN, 2000, "cat-bills")

        assert self.pairs([payment, receipt], known=False) == {("payment", "receipt")}

    def test_Leg_WhenAnotherAccountsLegIsOfTheSameSize_IsNotPairedWithIt(self):
        holiday = leg_of("holiday", "starling-space-holiday", -2000, "cat-main")
        receipt = leg_of("receipt", MAIN, 2000, "cat-bills")

        assert self.pairs([holiday, receipt]) == set()

    def test_OrdinaryRows_WhenNoRowIsALeg_PairExactlyAsBefore(self):
        card = txn(entity_id="card", account="current", amount=-5000)
        payment = txn(entity_id="payment", account="credit-card", amount=5000)

        assert self.pairs([card, payment]) == {("card", "payment")}


class TestALegWhoseSpaceIsNotKnown:
    def test_MainInLeg_WhenItNamesACategoryNoAccountHolds_StillPairsWithARowOfItsSize(
        self, stores
    ):
        store = stores(
            ORDERS[0],
            main=[
                *main_feed(),
                space_arrival(
                    "f-back-unknown",
                    AMOUNT,
                    DAY,
                    counterPartyUid="cat-unheard-of",
                    counterPartyName="Elsewhere",
                    transactionTime=f"2026-09-{DAY:02}T10:00:02.000Z",
                ),
            ],
            space=[*space_feed(), bills_leg()],
            export_rows=BASE_EXPORT,
            aggregator=[],
        )

        receipt = row_of(store, MAIN, "f-back-unknown")
        leg = row_of(store, BILLS, "s-back")
        assert (leg.entity_id, receipt.entity_id) in set(store.confirmed_transfer_pairs())
