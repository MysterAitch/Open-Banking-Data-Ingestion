"""No known balance is tested by agreeing with another known balance for the same day.

THE FAULT, found by a reviewer reading the rule and measured on constructed accounts. Rule 2 of
`agreement` says a known balance counts as tested only by reproducing it rather than by defining
it. Two sources stating one figure for the earliest day made the second "reproduce" the first
across a stretch of no days and no transactions, so the account was said to be in agreement
through a day nothing had tested, and a protection could be pressed for it.

THE ACCOUNT, `everyday` of `test_balance_anchors` (rows through 2026-03-20, running sums 03-10
+6,750 and 03-20 +3,950). Known balances, with the answers decided before the first run:

  one source      a balance typed for 03-10 only: untested, not in agreement through any day.
  two, equal      the same typed balance and a statement's closing, both for 03-10, equal:
                  still untested, `through` None, no day tested.
  two, equal, and a later balance 03-20 that the rows reproduce: through is 03-20 and the tested
                  days are 03-20 alone; 03-10 is the earliest known day and never tests.
  protection      refused through 03-10 in both of those, accepted through 03-20 in the second.
  nil premise     an account created with its history held (nil at the end of the day before) is
                  tested by its FIRST known balance, which is the one thing the rule must not
                  take away: the nil is a premise, not a balance.
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.ingest.family_anchors import OPENED, families_of
from obdi.ingest.store import Store
from obdi.verify.agreement import (
    AGREES,
    HELD_UNMET,
    UNTESTED,
    derive_agreement,
    known_of_opening,
    standing_of,
)
from obdi.verify.balance_anchors import STATED, STATEMENT, Anchor, derive_opening, effective_opening
from obdi.verify.protection import ProtectionRefused, press
from obdi.verify.protection import tested_days as days_a_protection_may_reach
from test_balance_anchors import ACCOUNT, everyday
from test_ledger import txn

D = date
FIRST, LATER = D(2026, 3, 10), D(2026, 3, 20)
#: What the rows give: +6,750 by 03-10 and +3,950 by 03-20, from an opening of 93,250.
AT_FIRST, AT_LATER = 100000, 97200


def closing(day: date, figure: int) -> Anchor:
    return Anchor(day, figure, STATEMENT, stated_by="santander-cc-pdf")


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "anchors.sqlite3") as opened:
        everyday(opened)
        yield opened


def read(store, *extra: Anchor, typed: bool = True):
    if typed:
        extra = (Anchor(FIRST, AT_FIRST, STATED), *extra)
    opening = effective_opening(store, ACCOUNT, extra_anchors=extra)
    return opening, standing_of(opening, [ACCOUNT], None)


class TestTwoSourcesStatingOneDay:
    def test_Account_WhenOnlyOneSourceStatesTheFirstDay_IsUntested(self, store):
        _, standing = read(store)

        assert standing.own.state == UNTESTED
        assert standing.own.through is None

    def test_Account_WhenTwoSourcesStateTheSameFigureForTheFirstDay_IsStillUntested(self, store):
        _, standing = read(store, closing(FIRST, AT_FIRST))

        assert standing.own.state == UNTESTED
        assert standing.own.through is None
        assert standing.own.tested_count == 0
        assert standing.own.tested == ()

    def test_Account_WhenALaterBalanceIsReproduced_ThroughIsThatDayAndNotTheFirst(self, store):
        _, standing = read(store, closing(FIRST, AT_FIRST), closing(LATER, AT_LATER))

        assert standing.own.state == AGREES
        assert standing.own.through == LATER
        assert standing.own.tested == (LATER,)
        assert standing.own.tested_count == 1

    def test_Account_WhenTheLaterDayIsStatedByTwoSources_StillTestsTheTransactions(self, store):
        _, standing = read(
            store, closing(FIRST, AT_FIRST), closing(LATER, AT_LATER),
            Anchor(LATER, AT_LATER, STATED),
        )

        assert standing.own.through == LATER
        assert standing.own.tested == (LATER,)

    def test_Account_WhenTheLaterBalanceIsNotReproduced_NoDayIsTestedAndItIsHeldAtThatDay(
        self, store
    ):
        _, standing = read(store, closing(FIRST, AT_FIRST), closing(LATER, AT_LATER + 500))

        assert standing.own.state == HELD_UNMET
        assert standing.own.through is None
        assert standing.own.tested == ()


class TestAProtectionOverDaysNothingTested:
    def test_Protection_ThroughTheFirstDayStatedByTwoSources_IsRefused(self, store):
        opening, standing = read(store, closing(FIRST, AT_FIRST))

        assert days_a_protection_may_reach(opening, standing) == ()
        with pytest.raises(ProtectionRefused):
            press(store, ACCOUNT, FIRST.isoformat(), opening=opening, standing=standing)

    def test_Protection_ThroughTheFirstDayWhenALaterOneIsTested_IsRefusedAndTheLaterAccepted(
        self, store
    ):
        opening, standing = read(store, closing(FIRST, AT_FIRST), closing(LATER, AT_LATER))

        assert days_a_protection_may_reach(opening, standing) == (LATER,), (
            "03-10 is met but tests nothing"
        )
        with pytest.raises(ProtectionRefused):
            press(store, ACCOUNT, FIRST.isoformat(), opening=opening, standing=standing)
        press(store, ACCOUNT, LATER.isoformat(), opening=opening, standing=standing)
        assert store.protection_record(ACCOUNT) is not None

    def test_Protection_ThroughTheFirstDayWithOneSource_IsRefusedAsItAlwaysWas(self, store):
        opening, standing = read(store, Anchor(LATER, AT_LATER, STATED))

        assert days_a_protection_may_reach(opening, standing) == (LATER,)
        with pytest.raises(ProtectionRefused):
            press(store, ACCOUNT, FIRST.isoformat(), opening=opening, standing=standing)


NEW_CLOSING = D(2026, 2, 10)
NIL_ANCHORS = [
    Anchor(D(2026, 1, 1), 0, OPENED),
    Anchor(NEW_CLOSING, 48766, STATEMENT, stated_by="pdf"),
]
NEW_ROWS = [
    txn("new", "feed", "pay", D(2026, 1, 5), 50000, "Pay"),
    txn("new", "feed", "shop", D(2026, 1, 20), -1234, "Shop"),
]


def opened_at_nil(rows):
    return derive_agreement(known_of_opening(derive_opening("new", NIL_ANCHORS, rows)), ())


class TestTheFirstKnownBalanceOfAnAccountOpenedAtNil:
    """`new` was created on 2026-01-02 with nothing in it; 500.00 arrives on 01-05 and 12.34 is
    spent on 01-20; one statement closes on 2026-02-10 at 487.66. Every transaction held: adds up
    through 2026-02-10, one balance tested, as the release before this rule concluded. The spend
    not held: does not add up to the balance for 2026-02-10."""

    def test_Account_WhenATransactionIsMissing_DoesNotAddUpToItsOneKnownBalance(self):
        found = opened_at_nil(NEW_ROWS[:1])

        assert found.state == HELD_UNMET
        assert found.held is not None and found.held.day == NEW_CLOSING

    def test_Account_WhenEveryTransactionSinceItWasCreatedIsHeld_AddsUpThroughThatBalance(self):
        found = opened_at_nil(NEW_ROWS)

        assert found.state == AGREES
        assert found.through == NEW_CLOSING
        assert found.tested_count == 1

    def test_WholeFamilyCreatedAtNil_TestsItsFirstKnownBalanceToo(self, tmp_path):
        """The household of `bank_balance_corpus` opens at nil on 2026-08-31 and its first known
        balance is stated for that day: tested by the nil, for the account and the family."""
        from bank_balance_corpus import EXPORT_ROWS, at, balance_body, household
        from test_space_attribution import MAIN, MAP

        store = household(
            tmp_path, [(at(20, 13), balance_body(330500, 362500))], export=EXPORT_ROWS
        )
        try:
            families = families_of(store, MAP)
            reading = effective_opening(store, MAIN, families=families)
            standing = standing_of(reading, [MAIN, *families.spaces_of(MAIN)], None)
        finally:
            store.connection.close()

        first = D(2026, 8, 31)
        assert standing.own.known_from == first and first in standing.own.tested
        assert standing.whole is not None
        assert standing.whole.known_from == first and first in standing.whole.tested
