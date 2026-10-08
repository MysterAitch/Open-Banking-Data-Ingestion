"""What the ledger says of the bank's own feed status of each counted row.

The deployed page left two outgoing payments, reported booked by both the bank's
feed and the aggregator, that the export and the certified statement do not
list, and whose absence is the whole of a permanent difference. A feed item's
status is a candidate reason (a payment later REFUNDED may leave the balance
without any credit being added), and nothing recorded says which statuses the
counted rows carry, so this measures it. How a status is COUNTED is not changed
here (`providers.starling.STATUS_MAP`); the page only says what each reading
would be built on.

Every scenario varies `round_up_corpus` (a main account and one Space, September
2026, a Space-blind export that lists the payments at their own amounts), and its
answer is decided before the first run.

KNOWN ANSWERS (amounts in pence; a Parcel payment is 1200 on day 6, a refund
credit is 1200 on day 7, an unrelated Space arrival of 710 on day 9 gives the
account a change to explain where the Parcel does not):

    a REFUNDED Parcel payment, the export omits it, no credit
        the export's balance is above the rows by 1200 from day 8 on
        1 counted row carries REFUNDED, the export lists 0, 0 have a counter-item
        the change equals minus the sum of the REFUNDED rows, and leaving them
        out leaves nothing to explain
    a REFUNDED Parcel payment the export lists, with a refund credit it lists
        no difference from either; 1 REFUNDED row, listed, with a counter-item
        the 710 change is not explained by the REFUNDED row
    a Parcel whose status was SETTLED in one fetch and REFUNDED in a later one
        the newest landed status counts, whichever order the sources arrived in
    a feed item with a status the map does not list
        counted by name, no row made, and no balance moves
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from landing import rebuild_from_raw
from obdi.ingest.family_anchors import families_of
from obdi.verify.balance_anchors import effective_opening
from obdi.verify.fault_explanation import FEED_STATUS_LEFT_OUT, FEED_STATUS_ROWS
from round_up_corpus import card_payment, household_store, main_feed, space_feed
from test_export_cuts import Row
from test_export_dating import render
from test_reversed_and_unpaired_legs import orphan_in_space, parcel, refund
from test_space_attribution import MAIN, MAP

EIGHTH, TENTH = date(2026, 9, 8), date(2026, 9, 10)

COUNTER_ITEM_DEFINITION = (
    "A counter-item is a row of the opposite direction and equal size within three days."
)


@pytest.fixture
def make(tmp_path):
    opened = []

    def build(main=(), space=(), **more):
        store = household_store(tmp_path, [*main_feed(), *main], [*space_feed(), *space], **more)
        opened.append(store)
        assert rebuild_from_raw(store, account_map=MAP).problems == []
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


def facts_of(store) -> dict[str, tuple[int, int, int]]:
    """Feed status -> (counted rows, the export lists, with a counter-item), whole account."""
    explanation = walk_of(store).explanation
    assert explanation is not None
    return {
        found.status: (found.counted, found.listed, found.counter_item)
        for found in explanation.feed_statuses.by_status
    }


def refunded(**more: Any) -> dict[str, Any]:
    return parcel(status="REFUNDED", **more)


PARCEL_LISTED = Row("Parcel", -1200, 6, 6)
REFUND_LISTED = Row("ParcelBack", 1200, 7, 7)


class TestARefundedPaymentTheExportOmits:
    def test_StatedBalances_WhenTheExportOmitsARefundedPaymentAndNoCreditFollows_TheExportIsAbove(
        self, make
    ):
        store = make([refunded()])

        assert differences(store) == {EIGHTH: 1200, TENTH: 1200, date(2026, 9, 12): 1200}

    def test_WholeAccount_WhenARefundedPaymentIsOmittedWithNoCredit_CountsOneUnlistedUnreturned(
        self, make
    ):
        found = facts_of(make([refunded()]))

        assert found["REFUNDED"] == (1, 0, 0)
        assert found["SETTLED"][1] > 0

    def test_Change_WhenOnlyARefundedPaymentIsMissing_TheStatusTestsBothHold(self, make):
        explanation = walk_of(make([refunded()])).explanation

        (change,) = explanation.changes
        assert FEED_STATUS_ROWS in change.holds
        assert FEED_STATUS_LEFT_OUT in change.holds
        (found,) = change.feed_status_rows
        assert (found.status, found.rows.count) == ("REFUNDED", 1)
        assert (found.equals, found.left) == ("equals minus", "nil")

    def test_Page_WhenARefundedPaymentIsOmitted_NamesItsFeedStatusAndBothTests(self, make):
        page = render(make([refunded()]))

        assert (
            "1 counted row carries the feed status REFUNDED, the export does not list it, "
            "and it has no counter-item."
        ) in page
        assert COUNTER_ITEM_DEFINITION in page
        assert (
            "The change equals minus the sum of the 1 row whose feed status is REFUNDED "
            "in the window:"
        ) in page
        assert "booked; feed status REFUNDED" in page
        assert (
            "Leave the 1 row of feed status REFUNDED out of the count and nothing is left "
            "to explain."
        ) in page

    def test_Page_WhenARefundedPaymentIsOmitted_StillCountsItAsMoney(self, make):
        store = make([refunded()])

        (held,) = [t for t in store.transactions_for_account(MAIN) if t.source_id == "f-parcel"]
        assert held.status.value == "booked"
        assert not held.status.is_history

    def test_MaskedPage_WhenARefundedPaymentIsOmitted_ShowsNoFigure(self, make):
        page = render(make([refunded()], [orphan_in_space()]))

        for figure in ("1200", "12.00", "710", "7.10"):
            assert figure not in page, figure


class TestARefundedPaymentTheExportLists:
    def corpus(self, make):
        return make(
            [refunded(), refund()],
            [orphan_in_space()],
            export_extra=(PARCEL_LISTED, REFUND_LISTED),
        )

    def test_StatedBalances_WhenTheExportListsThePaymentAndItsRefund_OnlyTheUnrelatedRowDiffers(
        self, make
    ):
        assert differences(self.corpus(make)) == {TENTH: -710, date(2026, 9, 12): -710}

    def test_WholeAccount_WhenTheExportListsTheRefundedPaymentAndItsCredit_CountsListedAndReturned(
        self, make
    ):
        found = facts_of(self.corpus(make))

        assert found["REFUNDED"] == (1, 1, 1)

    def test_Change_WhenARefundedPaymentIsListedAndReturned_NeitherStatusTestHolds(self, make):
        (change,) = walk_of(self.corpus(make)).explanation.changes

        assert FEED_STATUS_ROWS not in change.holds
        assert FEED_STATUS_LEFT_OUT not in change.holds
        assert change.feed_status_rows == ()

    def test_Page_WhenARefundedPaymentIsListedAndReturned_SaysSoWithoutNamingATest(self, make):
        page = render(self.corpus(make))

        assert (
            "1 counted row carries the feed status REFUNDED, the export lists it, and it has "
            "a counter-item."
        ) in page
        assert "whose feed status is" not in page
        assert "of feed status" not in page


class TestAnItemWhoseStatusChanged:
    def test_WholeAccount_WhenSettledThenRefunded_TheNewestStatusCounts(self, make):
        found = facts_of(make([parcel(status="SETTLED")], main_refetches=([refunded()],)))

        assert found["REFUNDED"] == (1, 0, 0)

    def test_WholeAccount_WhenRefundedThenSettled_TheNewestStatusCountsAndNoneIsRefunded(
        self, make
    ):
        found = facts_of(make([refunded()], main_refetches=([parcel(status="SETTLED")],)))

        assert "REFUNDED" not in found

    def test_WholeAccount_WhenTheExportArrivesLast_TheNewestFeedStatusStillCounts(self, make):
        later = facts_of(
            make([parcel(status="SETTLED")], main_refetches=([refunded()],), export_last=True)
        )
        earlier = facts_of(
            make([refunded()], main_refetches=([parcel(status="SETTLED")],), export_last=True)
        )

        assert later["REFUNDED"] == (1, 0, 0)
        assert "REFUNDED" not in earlier

    def test_Row_WhenSettledThenRefunded_IsNamedWithItsNewestStatus(self, make):
        page = render(make([parcel(status="SETTLED")], main_refetches=([refunded()],)))

        assert "booked; feed status REFUNDED" in page
        assert "booked; feed status SETTLED" not in page.split("whose feed status")[-1][:400]


class TestAnItemWithAStatusTheMapDoesNotList:
    def archived(self) -> dict[str, Any]:
        return card_payment("f-archived", "Archived", 480, 6, status="ARCHIVED")

    def test_StatedBalances_WhenAnItemHasAnUnmappedStatus_NoRowIsMadeAndNothingMoves(self, make):
        store = make([self.archived()])

        assert differences(store) == {}
        assert not [t for t in store.transactions_for_account(MAIN) if t.source_id == "f-archived"]

    def test_Page_WhenAnItemHasAnUnmappedStatus_CountsItByName(self, make):
        page = render(make([self.archived()], [orphan_in_space()]))

        assert "Feed items with a status the map does not list, so no row: ARCHIVED (1)." in page
        assert "No feed item carries a status the map does not list" not in page

    def test_Page_WhenOnlyMappedStatusesArrive_SaysNoneIsUnlisted(self, make):
        declined = card_payment("f-declined", "Declined", 480, 6, status="DECLINED")
        page = render(make([declined], [orphan_in_space()]))

        assert "No feed item carries a status the map does not list." in page
        assert "so no row:" not in page
        assert "Feed items the map drops on purpose, which make no row: DECLINED (1)." in page

    def test_Page_WhenSeveralNamesAreUnmapped_ListsEachWithItsCount(self, make):
        items = [
            card_payment("f-a1", "A", 100, 6, status="ARCHIVED"),
            card_payment("f-a2", "B", 110, 6, status="ARCHIVED"),
            card_payment("f-h1", "C", 120, 6, status="HELD"),
        ]
        page = render(make(items, [orphan_in_space()]))

        assert "so no row: ARCHIVED (2), HELD (1)." in page


class TestTheStatusTestsAmongOtherCauses:
    def test_Change_WhenARefundedRowIsNotTheCause_NoStatusTestHolds(self, make):
        store = make([refunded()], [orphan_in_space(day=7)], export_extra=(PARCEL_LISTED,))

        (change,) = walk_of(store).explanation.changes

        assert differences(store)[EIGHTH] == -710
        assert FEED_STATUS_ROWS not in change.holds

    def test_Change_WhenARefundedRowAndAnotherUnlistedRowShareAWindow_TheRestSumToWhatIsLeft(
        self, make
    ):
        store = make([refunded()], [orphan_in_space(day=7)])

        (change,) = walk_of(store).explanation.changes

        assert differences(store)[EIGHTH] == 1200 - 710
        assert FEED_STATUS_ROWS not in change.holds
        (found,) = change.feed_status_rows
        assert found.left == "sum"
        assert FEED_STATUS_LEFT_OUT in change.holds

    def test_Page_WhenEveryCountedRowIsSettled_SaysSoAndNamesNoStatusTest(self, make):
        page = render(make([], [orphan_in_space()]))

        assert "counted rows carry the feed status SETTLED" in page
        assert "No feed item carries a status the map does not list." in page
        assert "whose feed status is" not in page
        assert "of feed status" not in page
