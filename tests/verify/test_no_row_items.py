"""The feed items that made no row, said beside the change they sit in.

Every scenario varies `feed_morning_corpus`; its docstring holds the answers decided
first. A DECLINED, ACCOUNT_CHECK, or unlisted-status item makes no row and is never
money; the page says it exists, how it sits beside the rows of the change, and counts
such items once for the account.

KNOWN ANSWERS, decided before the first run:

    the morning (declined 2468 at 05:57 London, payment 3699 at 06:11, same merchant)
        the change's window holds 1 item that is not a row: DECLINED, out, 14 minutes
        before the out row, of the same recipient and a different size
        the account holds 1 DECLINED item that makes no row, and 0 of unlisted status
        the balance moves by the top-up less the payment and by nothing for the item
    a DECLINED item of another merchant a day before the unlisted payment
        listed too, of a different recipient
    a payment SETTLED (or PENDING) in one fetch and DECLINED in a later one
        the row is void and not counted (`declined_items`), so the change has no difference
        left to explain; with the pass off, the store keeps the row, the item is a row and
        not "not a row": 0 listed, and the change equals the sum of the rows whose own feed
        item makes no row
    the same two fetches landed the other way round
        the newest status is SETTLED, so nothing is declined and the test does not hold
"""

from __future__ import annotations

from datetime import date

import pytest

from feed_morning_corpus import (
    FIGURES,
    PAYMENT_MINOR,
    TOP_UP_LISTED,
    TOP_UP_MINOR,
    WORDS,
    cafe_payment,
    declined_attempt,
    morning,
    top_up,
)
from landing import rebuild_from_raw
from obdi.ingest import rebuild
from obdi.ingest.family_anchors import families_of
from obdi.verify.balance_anchors import effective_opening
from obdi.verify.fault_explanation import NO_ROW_STATUS_ROWS
from round_up_corpus import (
    MAIN_BALANCE,
    balance,
    card_payment,
    household_store,
    main_feed,
    space_feed,
)
from test_feed_times import plain, render
from test_space_attribution import MAIN, MAP

ITEM = "The bank's feed also holds 1 item in this window that is not a row:"


@pytest.fixture
def make(tmp_path):
    opened = []

    def build(main=(), space=(), **more):
        directory = tmp_path / f"store-{len(opened)}"
        directory.mkdir()
        store = household_store(
            directory, [*main_feed(), *main], [*space_feed(), *space], **more
        )
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


def listed(make, **more):
    return make(morning(), export_extra=(TOP_UP_LISTED,), **more)


class TestADeclinedAttemptBesideTheSamePayment:
    def test_Balance_WhenAnAttemptIsDeclined_TheItemMovesNothingAndMakesNoRow(self, make):
        store = listed(make)

        assert balance(store, MAIN) == MAIN_BALANCE + TOP_UP_MINOR - PAYMENT_MINOR
        assert not [t for t in store.transactions_for_account(MAIN) if t.source_id == "f-declined"]

    def test_Page_WhenADeclinedAttemptPrecedesThePayment_ListsItAsNotARowBesideIt(self, make):
        page = plain(render(listed(make)))

        assert ITEM in page
        assert (
            "DECLINED out item at 05:57, 14 minutes before the out row at 06:11, "
            "of the same recipient and a different size"
        ) in page

    def test_Page_WhenThePaymentIsReportedBeforeTheAttempt_TheAnswerIsTheSame(self, make):
        store = make(
            [declined_attempt(), top_up(), cafe_payment()], export_extra=(TOP_UP_LISTED,)
        )

        assert "DECLINED out item at 05:57, 14 minutes before the out row at 06:11" in plain(
            render(store)
        )

    def test_Page_WhenTheDeclinedItemIsOfAnotherMerchant_SaysTheRecipientDiffers(self, make):
        other = card_payment(
            "f-declined", "Florist", 2468, 9, status="DECLINED",
            transactionTime="2026-09-09T04:57:00.000Z",
        )
        store = make([cafe_payment(), top_up(), other], export_extra=(TOP_UP_LISTED,))

        assert "of a different recipient and a different size" in plain(render(store))

    def test_Page_WhenTheDeclinedItemIsTheSameSize_SaysSoWithoutSayingTheSize(self, make):
        same = declined_attempt()
        same["amount"] = {"currency": "GBP", "minorUnits": PAYMENT_MINOR}
        store = make([cafe_payment(), top_up(), same], export_extra=(TOP_UP_LISTED,))

        page = plain(render(store))

        assert "of the same recipient and the same size" in page
        assert "3699" not in page

    def test_Page_WhenADeclinedItemSitsInNoChangesWindow_ItIsNotListedThere(self, make):
        lone = card_payment(
            "f-lone", "Lone", 777, 3, status="DECLINED",
            transactionTime="2026-09-03T04:57:00.000Z",
        )
        page = plain(render(make([cafe_payment(), top_up(), lone], export_extra=(TOP_UP_LISTED,))))

        assert "that is not a row" not in page
        assert "that are not rows" not in page
        assert "Feed items the map drops on purpose, which make no row: DECLINED (1)." in page

    def test_Page_WhenNothingIsDeclined_SaysNothingOfItemsThatAreNotRows(self, make):
        page = plain(render(make([cafe_payment(), top_up()], export_extra=(TOP_UP_LISTED,))))

        assert "that is not a row" not in page
        assert "that are not rows" not in page

    def test_Page_ForTheWholeAccount_CountsDroppedItemsApartFromUnlistedStatuses(self, make):
        archived = card_payment(
            "f-arch", "Arch", 5, 9, status="ARCHIVED", transactionTime="2026-09-09T04:50:00.000Z"
        )
        page = plain(render(make([*morning(), archived], export_extra=(TOP_UP_LISTED,))))

        assert "Feed items the map drops on purpose, which make no row: DECLINED (1)." in page
        assert "Feed items with a status the map does not list, so no row: ARCHIVED (1)." in page

    def test_Page_WhenAnItemIsOnlyEverListed_NoUnlistedSentenceClaimsAnUnlistedStatus(self, make):
        page = plain(render(listed(make)))

        assert "No feed item carries a status the map does not list." in page

    def test_MaskedPage_WhenAnItemIsListedAsNotARow_ShowsNoFigureDescriptionOrPayee(self, make):
        page = render(listed(make))

        for figure in FIGURES:
            assert figure not in page, figure
        for word in WORDS:
            assert word not in page, word


class TestTheCapOnTheListOfItems:
    def test_Page_WhenMoreItemsThanTheCapShareAWindow_NamesTheCapAndCountsTheRest(self, make):
        many = [
            card_payment(
                f"f-d{n}", "Cafe", 100 + n, 9, status="DECLINED",
                transactionTime=f"2026-09-09T03:{10 + n}:00.000Z",
            )
            for n in range(9)
        ]
        page = plain(render(make([cafe_payment(), top_up(), *many], export_extra=(TOP_UP_LISTED,))))

        assert "The bank's feed also holds 9 items in this window that are not rows:" in page
        assert "and 3 more (the page names at most 6)" in page


class TestARowWhoseItemTheBankLaterDeclined:
    """A payment made from an earlier fetch, whose item a later fetch reports DECLINED.

    The rule (`declined_items.void_declined_items`) makes such a row history, so the family
    holds no money for it: `test_declined_items` has every arrival order and the exceptions.
    """

    def settled_then_declined(self, make, **more):
        return make(
            [cafe_payment(), top_up()],
            export_extra=(TOP_UP_LISTED,),
            main_refetches=([cafe_payment(status="DECLINED")],),
            **more,
        )

    def test_Store_WhenAnItemIsSettledThenDeclined_TheRowIsVoidAndNotCounted(self, make):
        store = self.settled_then_declined(make)

        (held,) = [t for t in store.transactions_for_account(MAIN) if t.source_id == "f-cafe"]

        assert held.status.value == "void"
        assert balance(store, MAIN) == MAIN_BALANCE + TOP_UP_MINOR

    def test_Change_WhenAnItemIsSettledThenDeclined_LeavesNoRowWhoseItemMakesNoRow(self, make):
        explanation = walk_of(self.settled_then_declined(make)).explanation

        assert all(NO_ROW_STATUS_ROWS not in change.holds for change in explanation.changes)
        assert not explanation.changes, "the export omits the payment and so do the rows"

    def test_Store_WhenAPendingPaymentIsLaterDeclined_TheRowIsVoid(self, make):
        store = make(
            [cafe_payment(status="PENDING"), top_up()],
            export_extra=(TOP_UP_LISTED,),
            main_refetches=([cafe_payment(status="DECLINED")],),
        )

        (held,) = [t for t in store.transactions_for_account(MAIN) if t.source_id == "f-cafe"]

        assert held.status.value == "void"


class TestTheExplanationOfARowLeftCounted:
    """A row counted although its item is, in the newest landed feed, a status that makes no row.

    The pass voids such a row unless another source also lists it, so the explanation that
    names it is read with the pass off: it stands for a row left counted, whichever way.
    """

    @pytest.fixture(autouse=True)
    def without_the_pass(self, monkeypatch):
        monkeypatch.setattr(rebuild, "void_declined_items", lambda store: None)

    def settled_then_declined(self, make, **more):
        return make(
            [cafe_payment(), top_up()],
            export_extra=(TOP_UP_LISTED,),
            main_refetches=([cafe_payment(status="DECLINED")],),
            **more,
        )

    def test_Change_WhenAnItemIsSettledThenDeclined_EqualsTheRowsWhoseItemMakesNoRow(self, make):
        (change, *_) = walk_of(self.settled_then_declined(make)).explanation.changes

        assert NO_ROW_STATUS_ROWS in change.holds
        assert change.no_row_status_equals == "equals minus"
        assert change.no_row_status_rows.count == 1
        assert change.no_rows.count == 0, "the item is a row, so it is not one that is not a row"

    def test_Page_WhenAnItemIsSettledThenDeclined_SaysSoInTheOrderOfTests(self, make):
        page = plain(render(self.settled_then_declined(make)))

        assert (
            "The change equals minus the sum of the 1 row the store counts whose own feed item "
            "is, in the newest landed feed, a status that makes no row (DECLINED)."
        ) in page
        assert "Feed items the map drops on purpose" not in page

    def test_Change_WhenTheExportArrivesLast_TheAnswerIsTheSame(self, make):
        (change, *_) = walk_of(
            self.settled_then_declined(make, export_last=True)
        ).explanation.changes

        assert NO_ROW_STATUS_ROWS in change.holds

    def test_Change_WhenDeclinedThenSettled_NothingIsDeclinedAndTheTestDoesNotHold(self, make):
        store = make(
            [cafe_payment(status="DECLINED"), top_up()],
            export_extra=(TOP_UP_LISTED,),
            main_refetches=([cafe_payment()],),
        )

        (change, *_) = walk_of(store).explanation.changes

        assert NO_ROW_STATUS_ROWS not in change.holds
        assert change.no_rows.count == 0

    def test_Change_WhenRowsWereNotDeclined_TheTestDoesNotHold(self, make):
        (change, *_) = walk_of(listed(make)).explanation.changes

        assert NO_ROW_STATUS_ROWS not in change.holds
        assert date(2026, 9, 9) == change.day
