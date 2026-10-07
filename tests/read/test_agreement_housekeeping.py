"""The Overview says "not in agreement for 45 days" only of an account that is not in agreement.

The deployed Overview raised seven housekeeping items of one sentence, "X has known balances but is
in agreement through D, more than 45 days ago", for three different situations:

    (a) the account is in agreement through its LAST known balance and has rows after it: nothing
        is unmet, and the next statement has simply not been uploaded
    (b) the same, with NO row after it (a quiet savings account whose newest row is 489 days old):
        nothing to say at all
    (c) a known balance after that date is unmet, or a movement fault holds agreement back: the
        only one the sentence describes

KNOWN ANSWERS. Every account is `everyday` (rows through 2026-03-20: see test_balance_anchors),
judged on 2026-09-01, so every one is more than 45 days past its date:

    A  stated 03-05 1000.00, 03-10 980.00, 03-20 952.00 (all met), one more row on 03-28
           (a): in agreement through 03-20, rows after the last known balance
    B  the same balances and no row after 03-20
           (b): no item
    C  stated 03-05 1000.00, 03-10 980.00, 03-15 950.00 (the rows predict 947.00)
           (c): held back by the known balance for 03-15, and it also has rows after it
    D  the balances of B and a movement fault dated 03-12
           (c): held back by a movement fault dated 03-12
    E  as A, and as C

    A, B, C, D together     one item for C, one for D, and ONE item naming A alone
    A and E together        one item per account in (c) and one naming both in (a)
    an account in (a) and (c) at once
                            is named in the (c) item and never in the (a) item
"""

from __future__ import annotations

from datetime import date

import pytest

from obdi.ingest.store import Store
from obdi.read.overview import STALE_AGREEMENT_DAYS, standing_items_from
from obdi.verify.balance_anchors import record_stated_anchor
from obdi.verify.movement_completeness import MISSING, MovementCompleteness, RowCountFault
from obdi.verify.standing_data import AccountStanding, standings_for
from test_balance_anchors import everyday
from test_ledger import land, txn

D = date
TODAY = D(2026, 9, 1)

SETTLED = (("2026-03-05", "1000.00"), ("2026-03-10", "980.00"), ("2026-03-20", "952.00"))
UNMET = (("2026-03-05", "1000.00"), ("2026-03-10", "980.00"), ("2026-03-15", "950.00"))


def label(ref: str) -> str:
    return ref


def open_account(_ref: str) -> bool:
    return False


@pytest.fixture
def household(tmp_path):
    """Builds one account in its own store, and answers its standing."""
    opened: list[Store] = []

    def build(
        ref: str,
        stated: tuple[tuple[str, str], ...],
        *,
        later_row: bool = False,
        fault_on: date | None = None,
    ) -> dict[str, AccountStanding]:
        store = Store(tmp_path / f"{ref}.sqlite3")
        opened.append(store)
        everyday(store, account=ref)
        for day, amount in stated:
            record_stated_anchor(store, ref, day, amount)
        if later_row:
            land(store, "d-late", txn(ref, "src-a", f"late-{ref}", D(2026, 3, 28), -100, "LATE"))
        faults = (
            []
            if fault_on is None
            else [RowCountFault(ref, "src-a", fault_on, "out", MISSING, 1, 0)]
        )
        movement = MovementCompleteness(row_faults=faults)
        return dict(standings_for(store, [ref], families=None, movement=movement))

    yield build
    for store in opened:
        store.close()


def items_for(*accounts: dict[str, AccountStanding], closed=open_account, today=TODAY):
    merged: dict[str, AccountStanding] = {}
    for one in accounts:
        merged.update(one)
    return standing_items_from(merged, label, closed, today)


class TestAnAccountInAgreementThroughItsLastKnownBalance:
    def test_Items_WhenRowsFollowTheLastKnownBalance_IsOneItemAskingForTheNextStatement(
        self, household
    ):
        found = items_for(household("a", SETTLED, later_row=True))

        assert [i.kind for i in found] == ["statement-due"]
        assert found[0].message == (
            "1 account has rows after its last known balance and none in the last "
            f"{STALE_AGREEMENT_DAYS} days: a (since 2026-03-20)."
        )
        assert found[0].accounts == ("a",)
        assert "Upload the next statement" in found[0].remedy

    def test_Items_WhenNoRowFollowsTheLastKnownBalance_SaysNothing(self, household):
        assert items_for(household("b", SETTLED)) == []

    def test_Items_WhenInAgreementThroughTheLimitExactly_SaysNothing(self, household):
        assert items_for(household("a", SETTLED, later_row=True), today=D(2026, 5, 4)) == []

    def test_Items_WhenInAgreementThroughOneDayPastTheLimit_AsksForTheNextStatement(
        self, household
    ):
        found = items_for(household("a", SETTLED, later_row=True), today=D(2026, 5, 5))

        assert [i.kind for i in found] == ["statement-due"]

    def test_Items_WhenTheAccountIsClosed_SaysNothing(self, household):
        assert items_for(household("a", SETTLED, later_row=True), closed=lambda _r: True) == []

    def test_Items_WhenSeveralAccountsAreAwaitingStatements_NamesThemAllInOneItem(self, household):
        found = items_for(
            household("a", SETTLED, later_row=True),
            household("c", SETTLED, later_row=True),
            household("d", SETTLED, later_row=True),
        )

        assert [i.kind for i in found] == ["statement-due"]
        assert found[0].message == (
            f"3 accounts have rows after their last known balance and none in the last "
            f"{STALE_AGREEMENT_DAYS} days: a (since 2026-03-20), c (since 2026-03-20), "
            "and d (since 2026-03-20)."
        )
        assert found[0].accounts == ("a", "c", "d")

    def test_Items_WhenTwoAccountsAreAwaitingStatements_NamesThemWithAnd(self, household):
        found = items_for(
            household("a", SETTLED, later_row=True), household("c", SETTLED, later_row=True)
        )

        assert found[0].message.endswith("a (since 2026-03-20) and c (since 2026-03-20).")


class TestAnAccountHeldBack:
    def test_Items_WhenAKnownBalanceIsUnmet_IsOneItemNamingTheBalance(self, household):
        found = items_for(household("c", UNMET))

        assert [i.kind for i in found] == ["agreement-lapsed"]
        assert found[0].accounts == ("c",)
        assert "its transactions last added up to a known balance on 2026-03-10" in (
            found[0].message
        )
        assert f"more than {STALE_AGREEMENT_DAYS} days ago" in found[0].message
        assert "The transactions do not add up to the known balance for 2026-03-15" in (
            found[0].message
        )

    def test_Items_WhenAMovementFaultHoldsItBack_IsOneItemNamingTheFault(self, household):
        found = items_for(household("d", SETTLED, fault_on=D(2026, 3, 12)))

        assert [i.kind for i in found] == ["agreement-lapsed"]
        assert "its transactions last added up to a known balance on 2026-03-10" in (
            found[0].message
        )
        assert (
            "A check of the money moved found a problem dated 2026-03-12, so the transactions "
            "cannot be shown to add up from then on"
        ) in (
            found[0].message
        )

    def test_Items_WhenHeldBackButWithinTheLimit_SaysNothing(self, household):
        assert items_for(household("c", UNMET), today=D(2026, 4, 24)) == []

    def test_Items_WhenNeverInAgreementSinceTheFirstKnownBalance_SaysSoAndWhatHoldsItBack(
        self, household
    ):
        found = items_for(
            household("n", (("2026-03-05", "1000.00"), ("2026-03-10", "999.00")))
        )

        assert [i.kind for i in found] == ["agreement-lapsed"]
        assert (
            "its transactions have never added up to a known balance since its first, 2026-03-05"
            in found[0].message
        )
        assert "The transactions do not add up to the known balance for 2026-03-10" in (
            found[0].message
        )


class TestAccountsInSeveralSituations:
    def test_Items_WhenAccountsAreInEverySituation_GivesOneItemPerHeldBackAccountAndOneForTheRest(
        self, household
    ):
        found = items_for(
            household("a", SETTLED, later_row=True),
            household("b", SETTLED),
            household("c", UNMET),
            household("d", SETTLED, fault_on=D(2026, 3, 12)),
        )

        assert sorted((i.kind, i.accounts) for i in found) == [
            ("agreement-lapsed", ("c",)),
            ("agreement-lapsed", ("d",)),
            ("statement-due", ("a",)),
        ]

    def test_Items_WhenOneAccountIsHeldBackAndAlsoHasRowsAfterItsLastKnownBalance_IsInTheFirstOnly(
        self, household
    ):
        held_back = household("e", UNMET)
        awaiting = household("a", SETTLED, later_row=True)

        found = items_for(held_back, awaiting)

        by_kind = {i.kind: i.accounts for i in found}
        assert by_kind == {"agreement-lapsed": ("e",), "statement-due": ("a",)}
