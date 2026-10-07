"""Every transfer leg has exactly one partner, in the account it names.

The pairing pass records which two rows it paired; nothing asked whether the
pair is right. The household is the round-up corpus: a main account, one Space,
an ordinary transfer to it on day 2 and four card payments whose round-ups
arrive in the Space. Every scenario lands the sources in EVERY order, rebuilds,
and reads the pairs the rebuild confirmed.

KNOWN ANSWERS, worked by hand before the first run:

    (i)    the household as landed
           ten legs (five leaving main, five arriving in the Space), every one
           verified against the account it names, no fault
    (ii)   the Space's feed lacks the day-2 arrival
           one fault: the main account's day-2 leg leaving, no partner
    (iii)  the main account's day-2 leg is paired with a row in another Space
           than the one it names
           two faults: that leg names the Bills Space but its partner is in the
           Holiday Space, and the Bills arrival it should have is left with no partner
    (iv)   the main account's day-2 leg is paired with an ordinary payment
           two faults: that leg's partner is not a transfer leg, and the Bills
           arrival is left with no partner
    (v)    two legs claiming one partner (the store's schema refuses the state,
           so the rule is shown on the pairs themselves)
           the partner's two legs are each named as sharing it
    (vi)   a pair of ordinary rows (a current account paying a card)
           no leg: one pair whose counterpart account cannot be verified, and
           no fault while the two are opposite and equal
    (vii)  a leg paired with a row of its own direction, of another size, and
           three days away
           one fault of each kind
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.core.models import SourceTier, Transaction, TransactionStatus
from obdi.ingest.store import Store
from obdi.verify.movement_completeness import (
    NO_PARTNER,
    NOT_A_LEG,
    OUTSIDE_WINDOW,
    SHARED_PARTNER,
    WRONG_ACCOUNT,
    WRONG_DIRECTION,
    WRONG_SIZE,
    check_legs,
    movement_completeness,
)
from round_up_corpus import main_feed, space_feed
from test_space_attribution import BILLS, HOLIDAY, MAIN, MAP
from test_space_blind_rows_and_internal_legs import (
    BASE_EXPORT,
    ORDERS,
    corpus,
    order_id,
)

TOPUP_DAY = 2


def canonical(ref: str) -> str:
    return str(MAP.resolve(*ref.split(":", 1))) if ":" in ref else ref


def household(directory, order, *, space=None) -> Store:
    return corpus(
        directory,
        order,
        main=main_feed(),
        space=space_feed() if space is None else space,
        export_rows=BASE_EXPORT,
        aggregator=[],
    )


@pytest.fixture
def stores(tmp_path):
    opened: list[Store] = []

    def build(order, **kwargs) -> Store:
        directory = tmp_path / f"{len(opened)}"
        directory.mkdir()
        store = household(directory, order, **kwargs)
        opened.append(store)
        return store

    yield build
    for store in opened:
        store.close()


def entity_of(store: Store, account: str, uid: str) -> str:
    (found,) = [t for t in store.transactions_for_account(account) if t.source_id == uid]
    return found.entity_id


def plant_copy(store: Store, like: str, entity_id: str, account: str, *, leg: bool) -> None:
    store.connection.execute(
        "INSERT INTO transactions SELECT ?, ?, amount_minor, currency, value_date, booking_date, "
        "description, counterparty, status, source, tier, NULL, content_key, 7, artefact_digest, "
        "?, match_tier, matched_entity_id, raw, first_seen_at, last_seen_at "
        "FROM transactions WHERE entity_id = ?",
        (entity_id, account, int(leg), like),
    )


def repair(store: Store, debit: str, credit: str) -> None:
    store.connection.execute(
        "UPDATE transfer_pairs SET credit_entity_id = ? WHERE debit_entity_id = ?",
        (credit, debit),
    )
    store.connection.commit()


CASES = [pytest.param(order, id=order_id(order)) for order in ORDERS]


class TestTheHouseholdsLegs:
    @pytest.mark.parametrize("order", CASES)
    def test_Legs_WhenEveryLegIsPairedAsNamed_AreVerifiedWithNoFaultInAnyArrivalOrder(
        self, stores, order
    ):
        report = movement_completeness(stores(order), canonical)

        assert report.leg_faults == []
        assert (report.legs, report.legs_verified, report.legs_unverifiable) == (10, 10, 0)

    @pytest.mark.parametrize("order", CASES)
    def test_Legs_WhenTheSpacesFeedLacksTheArrival_NamesTheLegLeavingMainWithNoPartner(
        self, stores, order
    ):
        feed = [item for item in space_feed() if item["feedItemUid"] != "s-topup"]

        report = movement_completeness(stores(order, space=feed), canonical)

        (fault,) = report.leg_faults
        assert (fault.account, fault.day, fault.direction, fault.kind) == (
            MAIN,
            date(2026, 9, TOPUP_DAY),
            "out",
            NO_PARTNER,
        )
        assert fault.names == BILLS
        assert "2026-09-02 starling-personal (out, transfer leg): no partner" in fault.says()

    @pytest.mark.parametrize("order", CASES)
    def test_Legs_WhenPairedWithARowInAnotherSpace_NamesBothSpaces(self, stores, order):
        store = stores(order)
        leg = entity_of(store, MAIN, "f-topup")
        arrival = entity_of(store, BILLS, "s-topup")
        plant_copy(store, arrival, "holiday-arrival", HOLIDAY, leg=True)
        repair(store, leg, "holiday-arrival")

        faults = movement_completeness(store, canonical).leg_faults

        assert {(f.account, f.kind) for f in faults} == {
            (BILLS, NO_PARTNER),
            (MAIN, WRONG_ACCOUNT),
        }
        assert len(faults) == 2
        wrong = next(f for f in faults if f.kind == WRONG_ACCOUNT)
        assert (wrong.names, wrong.partner_account) == (BILLS, HOLIDAY)
        assert f"names {BILLS} but its partner is in {HOLIDAY}" in wrong.says()

    @pytest.mark.parametrize("order", CASES)
    def test_Legs_WhenPairedWithAnOrdinaryPayment_SaysThePartnerIsNotALeg(self, stores, order):
        store = stores(order)
        leg = entity_of(store, MAIN, "f-topup")
        arrival = entity_of(store, BILLS, "s-topup")
        plant_copy(store, arrival, "ordinary-in", BILLS, leg=False)
        repair(store, leg, "ordinary-in")

        faults = movement_completeness(store, canonical).leg_faults

        assert {(f.account, f.kind) for f in faults} == {
            (BILLS, NO_PARTNER),
            (MAIN, NOT_A_LEG),
        }
        assert len(faults) == 2


def leg(entity: str, account: str, minor: int, day: int, *, names: str | None = None, leg_=True):
    when = date(2026, 9, day)
    return Transaction(
        account_id=account,
        amount_minor=minor,
        value_date=when,
        booking_date=when,
        description="x",
        source="starling",
        source_id=entity,
        content_key=entity,
        tier=SourceTier.AUTHORITATIVE,
        status=TransactionStatus.BOOKED,
        is_internal_transfer=leg_,
        entity_id=entity,
        raw={} if names is None else {"counterPartyUid": names},
    )


NAMES = {"cat-bills": BILLS, "cat-holiday": HOLIDAY}.get


class TestThePairsThemselves:
    def test_Legs_WhenTwoLegsClaimOnePartner_BothAreNamedAsSharingIt(self):
        rows = [
            leg("out-1", MAIN, -100, 4, names="cat-bills"),
            leg("out-2", MAIN, -100, 4, names="cat-bills"),
            leg("in-1", BILLS, 100, 4),
        ]

        _, _, _, _, faults = check_legs(rows, [("out-1", "in-1"), ("out-2", "in-1")], NAMES)

        assert sorted(f.kind for f in faults if f.kind == SHARED_PARTNER) == [
            SHARED_PARTNER,
            SHARED_PARTNER,
            SHARED_PARTNER,
        ]
        assert {f.account for f in faults if f.kind == SHARED_PARTNER} == {MAIN, BILLS}

    def test_PairingTable_WhenAPartnerIsClaimedTwice_RefusesTheState(self, tmp_path):
        import sqlite3

        with Store(tmp_path / "s.sqlite3") as store:
            store.replace_transfer_pairs([("out-1", "in-1")])
            with pytest.raises(sqlite3.IntegrityError):
                store.replace_transfer_pairs([("out-1", "in-1"), ("out-2", "in-1")])

    def test_Legs_WhenAPairIsOrdinaryRows_SaysTheCounterpartCannotBeVerified(self):
        rows = [
            leg("pay", "current", -5000, 4, leg_=False),
            leg("card", "card", 5000, 4, leg_=False),
        ]

        legs, _, _, unverifiable_pairs, faults = check_legs(rows, [("pay", "card")], NAMES)

        assert (legs, unverifiable_pairs, faults) == (0, 1, [])

    def test_Legs_WhenAnOrdinaryPairIsNotOppositeAndEqual_ReportsIt(self):
        rows = [
            leg("pay", "current", -5000, 4, leg_=False),
            leg("card", "card", 4000, 4, leg_=False),
        ]

        _, _, _, _, faults = check_legs(rows, [("pay", "card")], NAMES)

        assert [(f.kind, f.what) for f in faults] == [(WRONG_SIZE, "payment pair")]

    def test_Legs_WhenAPartnerMovesTheSameWayIsAnotherSizeAndFarAway_ReportsEachKind(self):
        rows = [
            leg("a", MAIN, -100, 1, names="cat-bills"),
            leg("b", BILLS, -100, 1),
            leg("c", MAIN, -100, 1, names="cat-bills"),
            leg("d", BILLS, 150, 1),
            leg("e", MAIN, -100, 1, names="cat-bills"),
            leg("f", BILLS, 100, 5),
        ]

        _, _, _, _, faults = check_legs(rows, [("a", "b"), ("c", "d"), ("e", "f")], NAMES)

        kinds = {(f.account, f.day.day, f.kind) for f in faults if f.account == MAIN}
        assert kinds == {
            (MAIN, 1, WRONG_DIRECTION),
            (MAIN, 1, WRONG_SIZE),
            (MAIN, 1, OUTSIDE_WINDOW),
        }

    def test_Legs_WhenALegNamesNoResolvableAccount_IsCountedAsUnverifiable(self):
        rows = [
            leg("a", MAIN, -100, 1, names="cat-unknown"),
            leg("b", BILLS, 100, 1, names="cat-unknown"),
        ]

        legs, verified, unverifiable, _, faults = check_legs(rows, [("a", "b")], NAMES)

        assert (legs, verified, unverifiable, faults) == (2, 0, 2, [])
