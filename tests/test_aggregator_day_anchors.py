"""The aggregator's running balance is a checkpoint at the end of each day with one chain.

The bank states a balance on every booked record. Until now only the earliest
opening and the latest closing of that chain were anchors, so a fault between them
could be located no closer than "somewhere in the history". A day whose records form
ONE chain states the balance at its own end (`AccountReconciliation.balances`).

WHAT THE BALANCE MEANS for a Starling main account is not assumed. The aggregator is
blind to Spaces in the account map, and `balance_meaning` decides whether its balance
moves with every payment from every pot (the whole account) or only with the main
account's own, from the steps of its own arithmetic. Two invented households, in
pence, each with a known answer worked out before the first run.

    day  what                                 main      Bills
     1   transfer main -> Bills (both legs)  -20000    +20000
     2   salary                              +50000
     3   Bills pays the water bill                      -5000
     4   coffee                               -2000
     5   transfer main -> Bills (both legs)  -10000    +10000
     6   Bills pays the gym                             -3000
     7   grocer                               -7000
     9   Bills pays a bill                              -4000
    10   a payment from main                  -1500
    12   Bills pays a bill                              -2500

The whole account held 1000.00 before the 1st, all in main.

WHOLE-ACCOUNT AGGREGATOR: lists every external payment, Space payments included, and its
balance is the family's: 1500.00 after the salary, then 1450, 1430, 1400, 1330, 1290,
1275, and 1250. The fold holds the Space payments' copies as history, so the 3rd, 6th,
9th, and 12th have no booked record and state nothing. KNOWN ANSWER: the verdict is
whole; the stated balances are the end of the 1st (1000.00, the opening) and the closings
of the 2nd, 4th, 7th, and 10th; all agree.

MAIN-ONLY AGGREGATOR: lists only main's own payments, and its balance moves with the
transfers it does not list: 1300.00 after the salary, 1280, 1110 after the grocer (the
5th's transfer is in it), 1095. KNOWN ANSWER: the verdict is main; the anchors are main's
own bank balances at the end of the 1st (800.00), 2nd, 4th, 7th, and 10th; all agree, and
main's opening is 1000.00 either way.
"""

from __future__ import annotations

import json
from datetime import date

import pytest

from obdi.balance_anchors import (
    BANK,
    STATED,
    derive_opening,
    effective_opening,
    record_stated_anchor,
)
from obdi.balance_meaning import MAIN as READ_AS_MAIN
from obdi.balance_meaning import WHOLE
from obdi.balance_reconciliation import balance_reconciliation
from obdi.ingest.family_anchors import families_of
from obdi.ingest.pipeline import reconcile_batch
from obdi.ingest.providers import truelayer
from obdi.ingest.rebuild import parse_artefact_transactions
from obdi.ingest.store import Store
from test_balance_anchors import TRUELAYER_ACCOUNT, build_bank_account, opening_of
from test_space_attribution import BILLS, FEED, MAIN, MAP, Household, pay

D = date


def record(ident: str, amount: str, day: int, running: str) -> dict:
    return {
        "transaction_id": f"volatile-{ident}",
        "normalised_provider_transaction_id": ident,
        "timestamp": f"2026-09-{day:02}T10:00:00Z",
        "description": ident,
        "amount": amount,
        "currency": "GBP",
        "transaction_type": "DEBIT" if amount.startswith("-") else "CREDIT",
        "running_balance": {"amount": running, "currency": "GBP"},
    }


FEED_ROWS = [
    ("out", MAIN, -20000, 1, "To Bills", True),
    ("in", BILLS, 20000, 1, "From Main", True),
    ("water", BILLS, -5000, 3, "Water Co", False),
    ("out2", MAIN, -10000, 5, "To Bills", True),
    ("in2", BILLS, 10000, 5, "From Main", True),
    ("gym", BILLS, -3000, 6, "Gym", False),
    ("bill", BILLS, -4000, 9, "Bill", False),
    ("last", BILLS, -2500, 12, "Rent", False),
]

WHOLE_RECORDS = [
    ("salary", "500.00", 2, "1500.00"),
    ("water", "-50.00", 3, "1450.00"),
    ("coffee", "-20.00", 4, "1430.00"),
    ("gym", "-30.00", 6, "1400.00"),
    ("grocer", "-70.00", 7, "1330.00"),
    ("bill", "-40.00", 9, "1290.00"),
    ("misc", "-15.00", 10, "1275.00"),
    ("last", "-25.00", 12, "1250.00"),
]

MAIN_ONLY_RECORDS = [
    ("salary", "500.00", 2, "1300.00"),
    ("coffee", "-20.00", 4, "1280.00"),
    ("grocer", "-70.00", 7, "1110.00"),
    ("misc", "-15.00", 10, "1095.00"),
]


def household(store: Store, records: list[tuple], *, omit: str = "") -> Household:
    home = Household(store, MAP)
    home.arrive(*(pay(a, FEED, f"f-{i}", m, d, w, internal=t) for i, a, m, d, w, t in FEED_ROWS))
    landed = [record(*r) for r in records if r[0] != omit]
    artefact = truelayer.artefact_for(
        json.dumps({"results": landed}).encode(), account_id="tl-main", kind="booked"
    )
    store.land_artefact(artefact)
    reconcile_batch(
        store,
        parse_artefact_transactions("truelayer-booked", artefact.payload, MAIN, artefact.digest),
        digest=artefact.digest,
    )
    home.settle()
    return home


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "family.sqlite3") as opened:
        yield opened


def opened(home: Household):
    return effective_opening(home.store, MAIN, families=families_of(home.store, MAP))


def verdict(opening) -> str:
    (meaning,) = [m for m in opening.meanings if m.source == "truelayer"]
    return meaning.verdict


class TestAWholeAccountAggregator:
    def test_Meaning_WhenTheAggregatorListsEverySpacePayment_IsReadAsTheWholeAccount(self, store):
        opening = opened(household(store, WHOLE_RECORDS))

        assert verdict(opening) == WHOLE

    def test_Anchors_WhenTheAggregatorIsTheWholeAccount_EveryClosingIsAFamilyCheckThatAgrees(
        self, store
    ):
        opening = opened(household(store, WHOLE_RECORDS))

        assert opening.family is not None
        assert [(r.day.day, r.balance_minor) for r in opening.family.readings] == [
            (1, 100000),
            (2, 150000),
            (4, 143000),
            (7, 133000),
            (10, 127500),
        ]
        assert opening.family.sources == ("truelayer",)
        assert opening.family.differing == []

    def test_Meaning_WhenAFeedRowIsMissingFromAShortSeries_TheReadingIsUnprovenAndNoBalanceIsUsed(
        self, store
    ):
        """The coffee on the 4th is not held although the bank's balances reflect it. One
        step in three then fails, below the share a reading must explain, so neither
        reading is adopted and no aggregator balance is an anchor: a short series cannot
        tell a lost row from a wrong reading, and says so instead of guessing."""
        opening = opened(household(store, WHOLE_RECORDS, omit="coffee"))

        assert verdict(opening) == "neither"
        assert opening.family is None or opening.family.sources == ()
        assert not [r for r in opening.readings if r.anchor.source == "truelayer"]


class TestAMainOnlyAggregator:
    def test_Meaning_WhenTheAggregatorListsOnlyMainsPaymentsAndMovesWithTransfers_IsReadAsMain(
        self, store
    ):
        opening = opened(household(store, MAIN_ONLY_RECORDS))

        assert verdict(opening) == READ_AS_MAIN

    def test_Anchors_WhenTheAggregatorIsMainOnly_EveryClosingIsMainsOwnBankBalanceThatAgrees(
        self, store
    ):
        opening = opened(household(store, MAIN_ONLY_RECORDS))

        own = [r for r in opening.readings if r.anchor.basis == BANK]
        assert [(r.anchor.day.day, r.anchor.balance_minor) for r in own] == [
            (1, 80000),
            (2, 130000),
            (4, 128000),
            (7, 111000),
            (10, 109500),
        ]
        assert all(r.agrees is not False for r in own)
        assert opening.opening_minor == 100000


class TestWhatTheOldCheckAndTheNewAnchorsEachSay:
    """The day-by-day check compares the store's rows with the bank's movement per day and
    the anchors compare the balance with the rows' running sum. A row lost from the store
    is a fault to both, and each counts it once, in its own terms."""

    def test_Check_WhenARowIsMissingFromTheStore_TheCheckAndTheAnchorsEachCountItOnce(
        self, tmp_path
    ):
        with Store(tmp_path / "omitted.sqlite3") as held:
            build_bank_account(held, omit=("d",))
            account = next(
                a for a in balance_reconciliation(held).accounts
                if a.account_id == TRUELAYER_ACCOUNT
            )
            opening = opening_of(held, TRUELAYER_ACCOUNT)

        # The check: the gap between 03-03's closing and 03-05's opening, once.
        assert len(account.continuity_breaks) == 1
        assert account.day_mismatches == []
        # The anchors: the same row, as one fault from 03-05 on (the difference never changes).
        assert [r.anchor.day for r in opening.differing] == [D(2026, 3, 5), D(2026, 3, 6)]
        assert {r.difference_minor for r in opening.differing} == {-2000}

    def test_Anchors_WhenARowIsLostFromTheMiddleOfADay_ThatDayStatesNothing(self, tmp_path):
        """f, g, h on 03-06 with g not landed: the day's records form two chains, so no
        figure is stated for it, and the 03-05 closing is still the last anchor."""
        with Store(tmp_path / "middle.sqlite3") as held:
            build_bank_account(held, omit=("g",))

            opening = opening_of(held, TRUELAYER_ACCOUNT)

        assert [r.anchor.day for r in opening.readings][-1] == D(2026, 3, 5)


class TestAddingClosingsNeverMovesAnOpening:
    def test_Opening_WhenEveryDaysClosingBecomesAnAnchor_IsTheOpeningTheTwoEndsDefined(
        self, tmp_path
    ):
        with Store(tmp_path / "ends.sqlite3") as held:
            build_bank_account(held)
            opening = opening_of(held, TRUELAYER_ACCOUNT)
            rows = held.transactions_for_account(TRUELAYER_ACCOUNT)

        ends = derive_opening(
            TRUELAYER_ACCOUNT,
            [opening.readings[0].anchor, opening.readings[-1].anchor],
            rows,
        )
        assert (opening.opening_minor, opening.as_at) == (ends.opening_minor, ends.as_at)
        assert opening.readings[0].defines_opening is True
        assert opening.readings[0].anchor.day == D(2026, 3, 1)

    def test_Opening_WhenAnAccountsOwnOpeningWasAStatedBalance_TheBankClosingsAreOnlyChecks(
        self, tmp_path
    ):
        with Store(tmp_path / "stated.sqlite3") as held:
            build_bank_account(held)
            record_stated_anchor(held, TRUELAYER_ACCOUNT, "2026-02-28", "900.00")

            opening = opening_of(held, TRUELAYER_ACCOUNT)

        assert opening.readings[0].anchor.basis == STATED
        assert opening.opening_minor == 90000
        assert all(r.defines_opening is False for r in opening.readings[1:])

    def test_Dating_WhenARowsStoredDateIsADayLaterThanTheAggregatorGaveIt_TheClosingStillAgrees(
        self, tmp_path
    ):
        """Row c (+250.00) was given 03-03 by the aggregator and is stored as 03-04, as a
        row another source dated last would be. The aggregator's closing for 03-03 is
        judged with the row on the day the aggregator gave it, so it agrees; by the stored
        date it would differ by 250.00, and the opening is the one it always was."""
        with Store(tmp_path / "dated.sqlite3") as held:
            build_bank_account(held)
            held.connection.execute(
                "UPDATE transactions SET value_date = '2026-03-04' WHERE amount_minor = 25000"
            )
            held.connection.commit()

            opening = opening_of(held, TRUELAYER_ACCOUNT)

        assert opening.opening_minor == 100000
        assert [r.agrees for r in opening.readings[1:]] == [True, True, True, True]
        assert opening.readings[0].anchor.source == ""
