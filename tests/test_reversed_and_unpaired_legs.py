"""What the ledger says of reversed rows and of round-ups that did not become a paired leg.

Every scenario varies `round_up_corpus` (a main account and one Space, September
2026, a Space-blind export that lists the payments at their own amounts and
omits anything added here), and its answer is decided before the first run.

WHAT THIS DOES NOT SETTLE. Whether a REVERSED feed item is money is a fact about
the bank that no invented data can prove: the status is counted today, as every
other non-history row is, and the scenarios below pin that reading so a change of
it is a deliberate act. What they prove is that the page measures it: the
arithmetic tests and the whole-account counts are what the next real page is
read for.

KNOWN ANSWERS (amounts in pence; the export omits each added row):

    a reversed 1200 payment, day 6, no counter-item
        the stated balance exceeds the rows by 1200 from day 8 on, one change
        1 counted row is reversed, the export lists 0 of them, 0 have a counter-item
    the same with a booked 1200 refund on day 7
        the refund and the reversed payment cancel: no difference from them
        1 counted row is reversed, 0 listed, 1 has a counter-item
    a reversed 1200 payment with a 30 round-up that stays in the Space
        the leg is confirmed paired; the difference is the payment alone, 1200
    the same, the Space returning the 30 as an OUT item the main account reports IN
        two more pairs; the difference is still the payment alone, 1200
    the same, the Space's OUT item with no main-side row
        the Space's OUT leg is unpaired; the difference is 1200 + 30
    an incoming transfer in the Space with no main-side row, 710, day 9
        the stated balance is below the rows by 710 from day 10 on
        1 incoming Space leg has no partner, dated 2026-09-09
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from obdi.balance_anchors import effective_opening
from obdi.family_anchors import families_of
from obdi.rebuild import rebuild_from_raw
from round_up_corpus import (
    card_payment,
    household_store,
    main_feed,
    round_up_of,
    space_arrival,
    space_feed,
)
from test_export_dating import render
from test_space_attribution import MAIN, MAP


def parcel(*, round_up: Any = None, status: str = "REVERSED") -> dict[str, Any]:
    return card_payment("f-parcel", "Parcel", 1200, 6, round_up=round_up, status=status)


def refund() -> dict[str, Any]:
    return {
        "feedItemUid": "f-parcel-back",
        "amount": {"currency": "GBP", "minorUnits": 1200},
        "direction": "IN",
        "transactionTime": "2026-09-07T10:00:00.000Z",
        "source": "MASTER_CARD",
        "status": "SETTLED",
        "counterPartyName": "Parcel",
        "reference": "Parcel",
    }


def space_return(uid: str, minor: int, day: int) -> dict[str, Any]:
    """The Space handing money back to the main account, as the Space's feed reports it."""
    return space_arrival(uid, minor, day, direction="OUT")


def main_receipt(uid: str, minor: int, day: int) -> dict[str, Any]:
    """Money arriving in the main account from the Space, as the main feed reports it."""
    return space_arrival(
        uid,
        minor,
        day,
        counterPartyUid="cat-bills",
        counterPartyName="Bills",
        transactionTime=f"2026-09-{day:02}T10:00:02.000Z",
    )


def orphan_in_space(uid: str = "s-orphan", minor: int = 710, day: int = 9) -> dict[str, Any]:
    return space_arrival(uid, minor, day)


def main_orphan_out() -> dict[str, Any]:
    return card_payment(
        "f-lost",
        "Savings",
        710,
        9,
        source="INTERNAL_TRANSFER",
        counterPartyType="CATEGORY",
        counterPartyUid="cat-bills",
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


def counted_pairs(store) -> int:
    return len(store.confirmed_transfer_pairs())


EIGHTH, TENTH = date(2026, 9, 8), date(2026, 9, 10)


class TestAReversedPaymentTheExportOmits:
    def test_StatedBalances_WhenAReversedPaymentIsNotInTheExport_DifferByItsAmountFromItsWindow(
        self, make
    ):
        store = make([parcel()])

        assert differences(store) == {
            EIGHTH: 1200,
            date(2026, 9, 10): 1200,
            date(2026, 9, 12): 1200,
        }
        assert len(walk_of(store).changes) == 1

    def test_Page_WhenAReversedPaymentIsNotInTheExport_SaysTheChangeEqualsMinusTheReversedRows(
        self, make
    ):
        page = render(make([parcel()]))

        assert (
            "The change equals minus the sum of the 1 reversed row the store counts in the window"
            in page
        )

    def test_Page_WhenAReversedPaymentIsNotInTheExport_SaysLeavingItOutExplainsTheChange(
        self, make
    ):
        page = render(make([parcel()]))

        assert (
            "Leave the 1 reversed row out of the count and nothing is left to explain"
            in page
        )

    def test_Page_WhenAReversedPaymentHasNoCounterItem_CountsItAcrossTheWholeAccount(self, make):
        page = render(make([parcel()]))

        assert (
            "1 counted row is reversed. The export lists 0 of them, and 0 have a "
            "counter-item, a row of the opposite direction and equal size within three days."
        ) in page

    def test_Page_WhenARowIsNamed_SaysWhichAccountItIsInAndThatItHasNoCounterItem(self, make):
        page = render(make([parcel()]))

        assert "reversed; in the main account; no counter-item within three days" in page

    def test_Page_WhenNoRowIsReversed_SaysNoReversedRowAcceptsTheChange(self, make):
        page = render(make([], [orphan_in_space()]))

        assert "0 counted rows are reversed." in page
        assert "reversed row" not in page.replace("0 counted rows are reversed", "")


class TestAReversedRowBesideAnotherCause:
    def test_Page_WhenAReversedPaymentAndASurplusShareAWindow_SaysOnlyTheRemainderIsExplained(
        self, make
    ):
        store = make([parcel()], [orphan_in_space(day=7)])

        assert differences(store)[EIGHTH] == 1200 - 710
        page = render(store)
        assert (
            "Leave the 1 reversed row out of the count and the unlisted rows still counted "
            "sum to what is left, exactly."
        ) in page
        assert "The change equals minus the sum of the 1 reversed row" not in page

    def test_Page_WhenAReversedRowIsMoneyIn_SaysTheChangeEqualsMinusItsSumToo(self, make):
        incoming = {**refund(), "feedItemUid": "f-incoming", "status": "REVERSED"}
        incoming["transactionTime"] = "2026-09-06T10:00:00.000Z"
        store = make([incoming])

        assert differences(store)[EIGHTH] == -1200
        page = render(store)
        assert "The change equals minus the sum of the 1 reversed row the store counts" in page
        assert "in row dated" in page


class TestAReversedPaymentWithACounterItem:
    def test_StatedBalances_WhenARefundCancelsTheReversedPayment_TheyStillAgreeThroughIt(
        self, make
    ):
        store = make([parcel(), refund()], [orphan_in_space()])

        assert differences(store) == {TENTH: -710, date(2026, 9, 12): -710}
        assert len(walk_of(store).changes) == 1

    def test_Page_WhenARefundCancelsTheReversedPayment_CountsTheCounterItem(self, make):
        page = render(make([parcel(), refund()], [orphan_in_space()]))

        assert "1 counted row is reversed. The export lists 0 of them, and 1 has a " in page


class TestARoundUpOnAReversedPayment:
    def test_Leg_WhenTheSpaceKeepsTheRoundUp_IsPairedAndTheDifferenceIsThePaymentAlone(self, make):
        store = make([parcel(round_up=round_up_of(30))], [space_arrival("s-parcel", 30, 6)])

        assert differences(store)[EIGHTH] == 1200
        assert counted_pairs(store) == 6
        page = render(store)
        assert "a round-up leg, confirmed paired with the Space starling-space-bills" in page
        assert "No round-up leg is without a pair in a Space." in page
        assert (
            "2 counted rows are reversed. The export lists 0 of them, and 0 have a "
            "counter-item"
        ) in page

    def test_Legs_WhenTheSpaceReturnsTheRoundUpAndTheMainAccountReportsIt_AllPairUp(self, make):
        store = make(
            [parcel(round_up=round_up_of(30)), main_receipt("f-back", 30, 7)],
            [space_arrival("s-parcel", 30, 6), space_return("s-back", 30, 7)],
        )

        assert differences(store)[EIGHTH] == 1200
        assert counted_pairs(store) == 7
        assert (
            "2 counted rows are reversed. The export lists 0 of them, and 1 has a counter-item"
        ) in render(store)

    def test_Leg_WhenTheSpacesReturnHasNoMainSideRow_IsNamedAsAnUnpairedLegInTheSpace(self, make):
        store = make(
            [parcel(round_up=round_up_of(30))],
            [space_arrival("s-parcel", 30, 6), space_return("s-back", 30, 7)],
        )

        assert differences(store)[EIGHTH] == 1230
        page = render(store)
        assert "a transfer leg with no pair; in the Space starling-space-bills" in page

    def test_Leg_WhenTheSpaceHoldsNothingForAReversedPayment_IsCountedAsOnAReversedPayment(
        self, make
    ):
        store = make([parcel(round_up=round_up_of(30))])

        gaps = walk_of(store).round_up_gaps

        assert (gaps.unpaired_legs, gaps.unpaired_on_reversed) == (1, 1)
        assert gaps.unpaired_to_unheld_space == gaps.unpaired_other == 0
        assert gaps.unpaired_days == (date(2026, 9, 6),)


class TestAnOrdinaryUnpairedTransferLeg:
    def test_Leg_WhenTheMainSideIsMissing_StaysADifferenceAndIsNamedWithItsAccount(self, make):
        store = make([], [orphan_in_space()])

        assert differences(store) == {TENTH: -710, date(2026, 9, 12): -710}
        page = render(store)
        assert "in row dated" in page
        assert "a transfer leg with no pair; in the Space starling-space-bills" in page

    def test_Leg_WhenTheSpaceSideIsMissing_StaysADifferenceAndIsNamedAsMainsOwn(self, make):
        store = make([main_orphan_out()])

        assert differences(store)[TENTH] == 710
        assert "a transfer leg with no pair; in the main account" in render(store)

    def test_Mirror_WhenASpaceHoldsAnIncomingLegWithNoMainPartner_CountsItAndDatesIt(self, make):
        store = make([], [orphan_in_space()])

        gaps = walk_of(store).round_up_gaps

        assert gaps.space_in_unpaired == 1
        assert gaps.space_in_unpaired_days == (date(2026, 9, 9),)
        assert (
            "1 incoming transfer leg in a Space has no partner in the main account"
            in render(store)
        )

    def test_Mirror_WhenMoreThanTwentyAreUnpaired_NamesTwentyDatesThenCountsTheRest(self, make):
        orphans = [orphan_in_space(f"s-o{d}", 1000 + d, d) for d in range(1, 26)]
        store = make([], orphans)

        page = render(store)

        gaps = walk_of(store).round_up_gaps
        assert gaps.space_in_unpaired == 25
        assert len(gaps.space_in_unpaired_days) == 20
        assert "and 5 more" in page


class TestRoundUpsThatNeverBecameALeg:
    def corpus(self, make):
        incoming = {
            "feedItemUid": "f-in-ru",
            "amount": {"currency": "GBP", "minorUnits": 400},
            "direction": "IN",
            "transactionTime": "2026-09-04T10:00:00.000Z",
            "source": "MASTER_CARD",
            "status": "SETTLED",
            "roundUp": round_up_of(40),
        }
        declined = card_payment(
            "f-declined", "Declined", 500, 4, round_up=round_up_of(40), status="DECLINED"
        )
        unreadable = card_payment("f-bad", "Bad", 300, 4, round_up="fifty pence")
        transfer = card_payment(
            "f-xfer",
            "Savings",
            900,
            4,
            round_up=round_up_of(40),
            source="INTERNAL_TRANSFER",
            counterPartyType="CATEGORY",
            counterPartyUid="cat-bills",
        )
        gone = card_payment("f-gone", "Gone", 350, 11, round_up=round_up_of(30, "cat-gone"))
        return make([incoming, declined, unreadable, transfer, gone])

    def test_Carriers_WhenNoLegWasMade_AreCountedByWhyAndTheSumsAgree(self, make):
        store = self.corpus(make)

        gaps = walk_of(store).round_up_gaps

        # Carriers: five in the household, and the five added here.
        assert walk_of(store).round_ups.carried == 10
        assert walk_of(store).round_ups.legs == 5
        assert gaps.no_leg == 5
        assert (
            gaps.no_leg_of_nothing,
            gaps.no_leg_incoming,
            gaps.no_leg_reversed_or_declined,
            gaps.no_leg_unreadable,
            gaps.no_leg_other,
        ) == (1, 1, 1, 1, 1)

    def test_Legs_WhenNoRowPairsThem_AreCountedByWhyAndDated(self, make):
        store = self.corpus(make)

        gaps = walk_of(store).round_up_gaps

        assert gaps.unpaired_legs == 1
        assert gaps.unpaired_to_unheld_space == 1
        assert gaps.unpaired_days == (date(2026, 9, 11),)

    def test_Page_WhenRoundUpsDidNotBecomeLegs_SaysWhyInCounts(self, make):
        page = render(self.corpus(make))

        assert (
            "Of the 5 feed rows that carry a round-up and hold no leg, 1 is a round-up of "
            "nothing, 1 is on an incoming item, 1 is on a reversed or declined item, 1 could "
            "not be read, and 1 is other."
        ) in page
        assert (
            "Of the 1 round-up leg that has no pair in a Space, 0 are on a reversed payment, "
            "1 goes to a Space whose rows are not held, and 0 are other."
        ) in page
        assert "2026-09-11" in page

    def test_Page_WhenEveryRoundUpIsALegAndPaired_SaysNoneIsMissing(self, make):
        page = render(make())

        assert "Of the 1 feed row that carries a round-up and holds no leg" in page
        assert "No round-up leg is without a pair in a Space." in page

    def test_MaskedPage_WhenRoundUpsFailedToPair_ShowsNoFigure(self, make):
        page = render(make([parcel(round_up=round_up_of(30))], [orphan_in_space()]))

        for figure in ("1200", "12.00", "1230", "12.30", "710", "7.10"):
            assert figure not in page, figure
