"""A Space's own balance, stated by each listing of its account, is a checkpoint for that Space.

Every pull lands the provider's listing of the account's Spaces. A listing carries each
savings goal's `totalSaved`, a currency and minor units, which is the Space's own
balance at the moment the listing was fetched. Until now a Space had no checkpoint of its
own, so a missed arrival into a Space could only be seen as a gap in the family's walk.
Each listing is judged exactly as the account's own balance is (`bank_balances`): at its
fetch instant, against the rows as the feed's own times say they stood then.

The household is `bank_balance_corpus`: the Bills Space is topped up by 400.00 on the
2nd at 10:00 and pays 50.00 on the 12th and 30.00 on the 20th, both at noon.

KNOWN ANSWERS, decided before the first run (Bills, in pence):

    held at the 15th, 20:00     40000 - 5000                      = 35000
    held at the 20th, 11:00     the same: the gym is paid at noon = 35000
    held at the 22nd, 20:00     35000 - 3000                      = 32000

    a Space whose rows are complete     every listing agrees
    the topup's arrival is not held     the 22nd's listing is 40000 above the rows
    the 22nd's listing omits the Space  one anchor, from the 15th; the omission is said
    a listing with no totalSaved        no anchor; one balance refused, with the reason

A Space has no opening of its own to define, so its listings are tested against rows that
start from nil, and no opening is derived or sent for it.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from typing import Any

import pytest

from bank_balance_corpus import (
    CLEARED_22,
    TOTAL_22,
    UID,
    at,
    balance_body,
    bills_items,
    household,
)
from obdi import bank_balances
from obdi.actual_push import opening_balances
from obdi.balance_anchors import ASSUMED_NIL, BANK, STATED, effective_opening, record_stated_anchor
from obdi.family_anchors import families_of
from obdi.providers import starling
from obdi.replay import ActualAccountBinding
from obdi.store import Store
from test_space_attribution import BILLS, HOLIDAY, MAIN, MAP

BILLS_AT_15 = 35000
BILLS_AT_22 = 32000


def goal(uid: str, saved: int | None, name: str = "A goal") -> dict[str, Any]:
    found: dict[str, Any] = {"savingsGoalUid": uid, "name": name}
    if saved is not None:
        found["totalSaved"] = {"currency": "GBP", "minorUnits": saved}
    return found


def listing(*goals: dict[str, Any]) -> bytes:
    return json.dumps({"savingsGoals": list(goals)}).encode("utf-8")


def land_listing(store: Store, body: bytes, fetched: datetime) -> None:
    artefact = starling.artefact_for(
        body,
        account_id=f"starling:{UID}",
        kind="spaces",
        origin=f"{starling.API_HOST}/api/v2/account/{UID}/savings-goals",
    )
    store.land_artefact(replace(artefact, fetched_at=fetched))


@pytest.fixture
def make(tmp_path):
    opened = []

    def made(*listings: tuple[datetime, bytes], bills=None, balances=(), where: str = "held"):
        directory = tmp_path / where
        directory.mkdir()
        store = household(directory, list(balances), bills=bills)
        opened.append(store)
        for fetched, body in listings:
            land_listing(store, body, fetched)
        return store

    yield made
    for store in opened:
        store.close()


def bills_opening(store: Store):
    return effective_opening(store, BILLS, families=families_of(store, MAP))


def listed(opening):
    return [r for r in opening.readings if r.anchor.basis == BANK]


def complete():
    return (
        (at(15, 20), listing(goal("cat-bills", BILLS_AT_15), goal("cat-holiday", 0))),
        (at(22, 20), listing(goal("cat-bills", BILLS_AT_22), goal("cat-holiday", 0))),
    )


class TestASpaceWhoseRowsAreComplete:
    def test_Listings_WhenTheSpacesRowsAreComplete_EveryListedBalanceAgrees(self, make):
        opening = bills_opening(make(*complete()))

        found = listed(opening)
        assert [(r.anchor.day.day, r.anchor.balance_minor) for r in found] == [
            (15, BILLS_AT_15),
            (22, BILLS_AT_22),
        ]
        assert [r.agrees for r in found] == [True, True]

    def test_Listing_WhenFetchedBeforeAPaymentWasMadeThatDay_IsJudgedAgainstTheRowsThen(
        self, make
    ):
        """The gym is paid at noon on the 20th. A listing fetched at 11:00 states 350.00 and
        agrees; judged by day alone the gym would count and it would differ by 30.00."""
        opening = bills_opening(
            make((at(20, 11), listing(goal("cat-bills", BILLS_AT_15))))
        )

        (only,) = listed(opening)
        assert only.agrees is True

    def test_Listing_WhenItAlsoNamesAnotherSpace_EachSpaceIsJudgedByItsOwnFigure(self, make):
        store = make(*complete())

        found = families_of(store, MAP)
        holiday = effective_opening(store, HOLIDAY, families=found)

        # The Holiday Space holds no rows and its listings state nothing saved: both agree.
        assert [r.anchor.balance_minor for r in listed(holiday)] == [0, 0]
        assert [r.agrees for r in listed(holiday)] == [True, True]


class TestASpaceMissingOneArrival:
    def test_Listing_WhenTheTopupsArrivalIsNotHeld_DiffersByExactlyIt(self, make):
        without_topup = [i for i in bills_items() if i["feedItemUid"] != "b-topup"]
        store = make(complete()[1], bills=without_topup)

        opening = bills_opening(store)

        (only,) = listed(opening)
        assert only.agrees is False
        assert only.difference_minor == 40000

    def test_MainAccount_WhenASpaceLacksAnArrival_ItsOwnBankCheckShowsNoDifference(self, make):
        without_topup = [i for i in bills_items() if i["feedItemUid"] != "b-topup"]
        store = make(
            complete()[1],
            bills=without_topup,
            balances=[(at(22, 20), balance_body(CLEARED_22, TOTAL_22))],
        )

        main = effective_opening(store, MAIN, families=families_of(store, MAP))

        own = [r for r in main.readings if r.anchor.source == bank_balances.BANK_SOURCE]
        assert all(r.agrees is not False for r in own)


class TestASpaceThatIsAbsentFromALaterListing:
    def test_Listing_WhenASpaceIsOmittedLater_NoAnchorFollowsAndTheOmissionIsSaid(self, make):
        store = make(
            (at(15, 20), listing(goal("cat-bills", BILLS_AT_15), goal("cat-holiday", 0))),
            (at(22, 20), listing(goal("cat-holiday", 0))),
        )

        opening = bills_opening(store)

        assert [r.anchor.day.day for r in listed(opening)] == [15]
        assert opening.bank is not None
        assert opening.bank.absent == 1
        assert bank_balances.describe(opening.bank)[-1].startswith(
            "The Space is omitted from the newest 1 listing of its account, the first on 2026-09-22"
        )

    def test_Listing_WhenASpaceIsOmittedThenListedAgain_NothingIsSaidToBeAbsent(self, make):
        store = make(
            (at(15, 20), listing(goal("cat-bills", BILLS_AT_15))),
            (at(18, 20), listing(goal("cat-holiday", 0))),
            (at(22, 20), listing(goal("cat-bills", BILLS_AT_22))),
        )

        opening = bills_opening(store)

        assert opening.bank is not None
        assert opening.bank.absent == 0
        assert len(listed(opening)) == 2


class TestAListingWithoutTheBalance:
    def test_Listing_WhenTheEntryHasNoTotalSaved_NoAnchorIsMadeAndTheRefusalIsCounted(self, make):
        store = make((at(22, 20), listing(goal("cat-bills", None))))

        opening = bills_opening(store)

        assert listed(opening) == []
        assert opening.bank is not None
        assert opening.bank.refused == ("the Space's entry in a listing: totalSaved is missing",)

    def test_Listing_WhenTheCurrencyIsNotGbp_NoAnchorIsMade(self, make):
        body = json.dumps(
            {"savingsGoals": [{"savingsGoalUid": "cat-bills", "totalSaved": {
                "currency": "EUR", "minorUnits": 100}}]}
        ).encode()
        opening = bills_opening(make((at(22, 20), body)))

        assert listed(opening) == []
        assert opening.bank is not None and len(opening.bank.refused) == 1

    def test_Listing_WhenTheBodyIsNotAListing_SaysNothingOfAnySpace(self, make):
        opening = bills_opening(make((at(22, 20), b"<html>gateway error</html>")))

        assert listed(opening) == []
        assert opening.bank is None


class TestTheCostOfListings:
    def test_Listings_WhenTheLedgerIsReadAgain_EachListingBodyIsParsedOnce(
        self, make, monkeypatch
    ):
        store = make(*complete())
        bank_balances._LISTED.clear()
        calls: list[int] = []
        real = bank_balances._read_listing
        monkeypatch.setattr(
            bank_balances, "_read_listing", lambda payload: calls.append(1) or real(payload)
        )

        bills_opening(store)
        first = len(calls)
        bills_opening(store)

        assert first == 2
        assert len(calls) == first


class TestNoOpeningIsDefinedByAListing:
    @staticmethod
    def pushed(store: Store) -> set[tuple[str, object, int]]:
        bindings = [
            ActualAccountBinding(BILLS, "act-bills"),
            ActualAccountBinding(MAIN, "act-main"),
        ]
        return {
            (o.canonical_id, o.as_at, o.amount_minor)
            for o in opening_balances(store, bindings, families=families_of(store, MAP))
        }

    def test_Opening_WhenASpaceHasOnlyItsListings_NoneIsDerivedOrSentAndNilIsOnlyAssumed(
        self, make
    ):
        before = self.pushed(make(where="bare"))

        store = make(*complete())
        opening = bills_opening(store)

        assert opening.opening_minor is None
        assert opening.as_at is None
        assert opening.readings[0].anchor.basis == ASSUMED_NIL
        assert BILLS not in {canonical for canonical, _, _ in self.pushed(store)}
        assert self.pushed(store) == before

    def test_Opening_WhenASpaceAlreadyHasAStatedOpening_ListingsAreOnlyChecksOfIt(self, make):
        store = make((at(15, 20), listing(goal("cat-bills", BILLS_AT_15))))
        record_stated_anchor(store, BILLS, "2026-09-22", "320.00")
        with_listing = bills_opening(store)

        bare = make(where="bare")
        record_stated_anchor(bare, BILLS, "2026-09-22", "320.00")
        without = bills_opening(bare)

        assert with_listing.readings[0].anchor.basis == STATED
        assert (with_listing.opening_minor, with_listing.as_at) == (
            without.opening_minor,
            without.as_at,
        )

    def test_Opening_WhenAListingFallsAfterAStatedOpening_ItIsATestOfIt(self, make):
        store = make((at(25, 20), listing(goal("cat-bills", BILLS_AT_22))))
        record_stated_anchor(store, BILLS, "2026-09-22", "320.00")

        opening = bills_opening(store)

        assert [r.anchor.basis for r in opening.readings] == [STATED, BANK]
        assert opening.readings[1].agrees is True


class TestThePageForASpace:
    @staticmethod
    def page(store: Store) -> str:
        import html

        from obdi.ledger import build_ledger
        from obdi.web_ledger import render_ledger

        ledger = build_ledger(store, BILLS, None, bound=False, families=families_of(store, MAP))
        return html.unescape(render_ledger(ledger, unmasked=False).decode("utf-8"))

    def test_Page_WhenASpaceListingDiffers_SaysSoWithNoFigure(self, make):
        without_topup = [i for i in bills_items() if i["feedItemUid"] != "b-topup"]
        page = self.page(make(complete()[1], bills=without_topup))

        assert "1 known balance differs" in page
        assert "No opening balance could be derived:</strong> the Space's rows are taken" in page
        assert "400.00" not in page
        assert "40000" not in page

    def test_Page_WhenASpaceIsOmittedFromANewerListing_SaysItIsOmitted(self, make):
        page = self.page(
            make(
                (at(15, 20), listing(goal("cat-bills", BILLS_AT_15))),
                (at(22, 20), listing(goal("cat-holiday", 0))),
            )
        )

        assert "The Space is omitted from the newest 1 listing of its account" in page
