"""A round-up that reached a Space is a leg whatever became of the payment that carried it.

The deployed page showed a Space's unpaired incoming leg beside a REVERSED row in
the main account that carried a round-up and held no leg, and 19 carriers on
reversed or declined items with no leg against 21 Space arrivals with no
partner. A reversed or declined payment's round-up had still arrived in the
Space, and the main account had nothing for it leaving.

Every scenario varies `round_up_corpus` (a main account and one Space, a
Space-blind export that lists the household's payments and omits anything added
here). Its answer is decided before the first run.

KNOWN ANSWERS (amounts in pence, the export omits each added row; the household
alone has five confirmed pairs):

    a reversed 1200 card payment, direction IN, a 30 round-up, arriving in the Space, day 6
        the row is held as history, not counted: the stated balances agree with
        the rows, which hold the 30 leg alone
        the main account holds a booked OUT leg of 30, paired with the arrival: six pairs
        no round-up leg is without a pair
    a declined 500 card payment with a 30 round-up, arriving in the Space, day 6
        the payment yields no row; the leg stands alone, booked, paired: six pairs
        the stated balances agree with the rows at every statement
    a reversed 1200 payment, IN, whose 30 round-up never arrived
        the leg exists and is unpaired, counted as on a reversed or dropped payment
        the stated balances differ by the leg alone, 30: the honest state
        the page says no Space arrival of that size lies within three days
    two reversed payments of the same day each with a 30 round-up and ONE arrival
        one leg pairs and one does not
        the unpaired leg's note says a Space arrival of that size lies within three days
    a booked 400 refund, direction IN, carrying a 40 round-up
        no leg: nothing says a refund's round-up moves money out
    a pending 1200 payment with a 30 round-up, then the same item reversed
        one leg, identity f-parcel:round-up, booked at the end, no duplicate
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from obdi.core.models import TransactionStatus
from obdi.ingest.family_anchors import families_of
from obdi.ingest.providers import starling
from obdi.ingest.rebuild import rebuild_from_raw
from obdi.verify.balance_anchors import effective_opening
from round_up_corpus import (
    card_payment,
    household_store,
    land_feed,
    main_feed,
    round_up_of,
    space_arrival,
    space_feed,
)
from test_export_dating import render
from test_family_anchors import FEED_ORIGIN
from test_space_attribution import MAIN, MAP

EIGHTH = date(2026, 9, 8)


def parcel(
    *,
    uid: str = "f-parcel",
    status: str = "REVERSED",
    direction: str = "IN",
    round_up: Any = None,
    minor: int = 1200,
) -> dict[str, Any]:
    return card_payment(
        uid,
        "Parcel",
        minor,
        6,
        round_up=round_up_of(30) if round_up is None else round_up,
        status=status,
        direction=direction,
    )


@pytest.fixture
def make(tmp_path):
    opened = []

    def build(main=(), space=()):
        store = household_store(tmp_path, [*main_feed(), *main], [*space_feed(), *space])
        opened.append(store)
        report = rebuild_from_raw(store, account_map=MAP)
        assert report.problems == []
        return store

    yield build
    for store in opened:
        store.close()


def walk_of(store):
    opening = effective_opening(store, MAIN, families=families_of(store, MAP))
    assert opening.family is not None
    return opening.family


def differences(store) -> dict[date, int]:
    return {r.day: r.difference_minor for r in walk_of(store).readings if r.difference_minor}


def legs_of(store, payment_uid: str):
    return [
        t
        for t in store.transactions_for_account(MAIN)
        if t.raw.get("roundUpOf") == payment_uid
    ]


class TestARoundUpOfAReversedPayment:
    def test_Leg_WhenAReversedInPaymentsRoundUpArrived_IsABookedLegPairedWithTheArrival(
        self, make
    ):
        store = make([parcel()], [space_arrival("s-parcel", 30, 6)])

        (leg,) = legs_of(store, "f-parcel")
        assert (leg.amount_minor, leg.status) == (-30, TransactionStatus.BOOKED)
        assert leg.source_id == "f-parcel:round-up"
        assert len(store.confirmed_transfer_pairs()) == 6

    def test_StatedBalances_WhenTheReversedPaymentIsMoneyIn_TheyAgreeWithTheLegAlone(self, make):
        store = make([parcel()], [space_arrival("s-parcel", 30, 6)])

        assert differences(store) == {}

    def test_ReversedRow_WhenItsRoundUpHasALeg_IsHeldAsHistoryAtItsOwnAmount(self, make):
        store = make([parcel()], [space_arrival("s-parcel", 30, 6), space_arrival("s-x", 710, 9)])

        rows = [
            t for t in store.transactions_for_account(MAIN) if t.source_id == "f-parcel"
        ]
        assert [(t.amount_minor, t.status) for t in rows] == [(1200, TransactionStatus.REVERSED)]
        assert "1 reversed row is held as history." in render(store)

    def test_Page_WhenTheReversedPaymentsRoundUpArrived_SaysNoLegIsWithoutAPair(self, make):
        page = render(make([parcel()], [space_arrival("s-parcel", 30, 6)]))

        assert "No round-up leg is without a pair in a Space." in page

    def test_Leg_WhenTheReversedPaymentIsMoneyOut_IsBookedToo(self, make):
        store = make(
            [parcel(direction="OUT")], [space_arrival("s-parcel", 30, 6)]
        )

        (leg,) = legs_of(store, "f-parcel")
        assert leg.status is TransactionStatus.BOOKED
        assert len(store.confirmed_transfer_pairs()) == 6

    def test_Leg_WhenTheReversedPaymentIsLeftAtNil_IsStillMadeAndThePaymentAddsNothing(self):
        # The shape a real account showed: its reversed rows added nothing to
        # any difference, read as money in only because nil is not money out,
        # and were counted as carriers on reversed items and not incoming ones.
        payment, leg = starling.to_transactions(
            card_payment("f-nil", "Parcel", 0, 6, status="REVERSED", round_up=round_up_of(30)),
            account_id=MAIN,
        )

        assert (payment.status, payment.amount_minor) == (TransactionStatus.REVERSED, 0)
        assert (leg.status, leg.amount_minor) == (TransactionStatus.BOOKED, -30)
        assert leg.is_internal_transfer


class TestARoundUpOfADeclinedPayment:
    def test_Leg_WhenThePaymentYieldsNoRow_StandsAloneBookedAndPaired(self, make):
        store = make(
            [parcel(uid="f-declined", status="DECLINED", direction="OUT", minor=500)],
            [space_arrival("s-declined", 30, 6)],
        )

        held = store.transactions_for_account(MAIN)
        assert [t for t in held if t.source_id == "f-declined"] == []
        (leg,) = legs_of(store, "f-declined")
        assert (leg.amount_minor, leg.status) == (-30, TransactionStatus.BOOKED)
        assert len(store.confirmed_transfer_pairs()) == 6

    def test_StatedBalances_WhenADeclinedPaymentsRoundUpArrived_AgreeAtEveryStatement(self, make):
        store = make(
            [parcel(uid="f-declined", status="DECLINED", direction="OUT", minor=500)],
            [space_arrival("s-declined", 30, 6)],
        )

        assert differences(store) == {}

    def test_Leg_WhenTheStatusIsOneTheMapDropsAltogether_IsMadeToo(self, make):
        store = make(
            [parcel(uid="f-check", status="ACCOUNT_CHECK", direction="OUT")],
            [space_arrival("s-check", 30, 6)],
        )

        assert len(legs_of(store, "f-check")) == 1
        assert differences(store) == {}


class TestARoundUpThatDidNotArrive:
    def test_Leg_WhenAReversedPaymentsRoundUpNeverArrived_IsUnpairedAndCountedAsOnAReversedPayment(
        self, make
    ):
        store = make([parcel()])

        gaps = walk_of(store).round_up_gaps
        assert (gaps.unpaired_legs, gaps.unpaired_on_reversed) == (1, 1)
        assert gaps.unpaired_to_unheld_space == gaps.unpaired_other == 0
        assert differences(store)[EIGHTH] == 30

    def test_Leg_WhenADeclinedPaymentsRoundUpNeverArrived_IsCountedAsOnAReversedPayment(
        self, make
    ):
        store = make([parcel(uid="f-declined", status="DECLINED", direction="OUT")])

        gaps = walk_of(store).round_up_gaps
        assert (gaps.unpaired_legs, gaps.unpaired_on_reversed) == (1, 1)

    def test_Leg_WhenABookedPaymentsRoundUpNeverArrived_IsNotCountedAsOnAReversedPayment(
        self, make
    ):
        store = make([parcel(uid="f-booked", status="SETTLED", direction="OUT")])

        gaps = walk_of(store).round_up_gaps
        assert (gaps.unpaired_legs, gaps.unpaired_on_reversed, gaps.unpaired_other) == (1, 0, 1)

    def test_Page_WhenNoSpaceArrivalOfThatSizeExists_SaysSo(self, make):
        page = render(make([parcel()]))

        assert "1 is on a reversed or dropped payment" in page
        assert "no Space arrival of the same size within three days" in page

    def test_Page_WhenTheSpaceHoldsAnArrivalThatPairedWithAnotherLeg_SaysOneLiesWithinThreeDays(
        self, make
    ):
        twin = parcel(uid="f-twin")
        store = make([parcel(), twin], [space_arrival("s-parcel", 30, 6)])

        gaps = walk_of(store).round_up_gaps
        assert gaps.unpaired_legs == 1
        page = render(store)
        assert "a Space arrival of the same size lies within three days" in page
        assert "no Space arrival of the same size within three days" not in page

    def test_MaskedPage_WhenALegIsUnpaired_ShowsNoFigure(self, make):
        page = render(make([parcel()]))

        for figure in ("1200", "12.00", "1170", "11.70"):
            assert figure not in page, figure


class TestARoundUpOnAnIncomingItem:
    def test_Leg_WhenABookedRefundCarriesARoundUp_IsNotMadeAndIsCountedAsOnAnIncomingItem(
        self, make
    ):
        refund = card_payment(
            "f-refund",
            "Refund",
            400,
            6,
            round_up=round_up_of(40),
            status="SETTLED",
            direction="IN",
        )
        store = make([refund])

        assert legs_of(store, "f-refund") == []
        assert walk_of(store).round_up_gaps.no_leg_incoming == 1

    def test_Leg_WhenAPendingIncomingItemCarriesARoundUp_IsNotMade(self, make):
        pending_in = parcel(uid="f-pending-in", status="PENDING")

        assert starling.to_transactions(pending_in, account_id=MAIN)[1:] == []


class TestAPaymentThatChangedStatus:
    def test_Leg_WhenAPendingPaymentLaterReverses_IsOneBookedLegAndNoDuplicate(self, tmp_path):
        store = household_store(tmp_path, main_feed(), [*space_feed(), space_arrival("s-p", 30, 6)])
        try:
            land_feed(
                store,
                [parcel(status="PENDING", direction="OUT")],
                origin=FEED_ORIGIN,
                asked="2026-09-07T00:00:00Z",
            )
            pending = rebuild_from_raw(store, account_map=MAP)
            assert pending.problems == []
            (first,) = legs_of(store, "f-parcel")
            assert first.status is TransactionStatus.PENDING

            land_feed(
                store,
                [parcel(status="REVERSED", direction="OUT")],
                origin=FEED_ORIGIN,
                asked="2026-09-08T00:00:00Z",
            )
            later = rebuild_from_raw(store, account_map=MAP)
            assert later.problems == []

            (leg,) = legs_of(store, "f-parcel")
            assert leg.status is TransactionStatus.BOOKED
            assert leg.source_id == "f-parcel:round-up"
            assert len(store.confirmed_transfer_pairs()) == 6
        finally:
            store.close()


class TestExistingLegsKeepTheirIdentity:
    def test_Legs_WhenTheHouseholdIsRebuilt_KeepTheirIdentitiesAndContentKeys(self, make):
        store = make()

        legs = {
            t.source_id: (t.amount_minor, t.status, t.content_key)
            for t in store.transactions_for_account(MAIN)
            if "roundUpOf" in t.raw
        }
        assert set(legs) == {
            "f-coffee:round-up",
            "f-grocer:round-up",
            "f-taxi:round-up",
            "f-lunch:round-up",
        }
        assert {amount for amount, _, _ in legs.values()} == {-50, -90, -25, -20}
        assert {status for _, status, _ in legs.values()} == {TransactionStatus.BOOKED}

    def test_Leg_WhenAPendingOrBookedOutgoingPaymentCarriesARoundUp_HasTheContentKeyItAlwaysHad(
        self,
    ):
        for status, expected in (
            ("SETTLED", TransactionStatus.BOOKED),
            ("PENDING", TransactionStatus.PENDING),
        ):
            item = card_payment("f-x", "Shop", 350, 3, round_up=round_up_of(50), status=status)
            payment, leg = starling.to_transactions(item, account_id=MAIN)

            assert leg.status is expected
            assert leg.source_id == "f-x:round-up"
            assert leg.value_date == payment.value_date
            assert leg.content_key == starling.content_key(
                amount_minor=-50, value_date=payment.value_date, description="Round-up"
            )
