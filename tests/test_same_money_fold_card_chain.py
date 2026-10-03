"""The same-money fold on a card held over nine statements with a month missing.

The corpus and every answer are in `card_chain_corpus`. This module asks the
questions: which feed rows are folded, which stay counted, and whether every
period then agrees where the answer says it can. The shape is the one a real
card's masked report showed (the fold, built twice from guesses about it, folded
nothing on it): the feed carries a statement's charge once, dated the first day
of the same period, a month before the statement's rows.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from card_chain_corpus import (
    AUGUST_CHARGE,
    CARD,
    CHARGES,
    CLOSINGS,
    GAP_POSITION,
    Card,
    build_card,
    charge_day_of,
    feed_row,
    land,
    pair_with_savings,
    statement_day,
    text_day,
)
from obdi.models import TransactionStatus
from obdi.period_reconciliation import PeriodKind, gather_evidence, period_reconciliation
from obdi.same_money_fold import fold_same_money, plan_same_money
from obdi.statement_membership import Membership
from obdi.store import Store
from test_period_reconciliation import _held_statement


@pytest.fixture
def store(tmp_path: Path):
    with Store(tmp_path / "chain.sqlite3") as opened:
        yield opened


def folded(store: Store) -> list[str]:
    return sorted(
        t.description
        for t in store.transactions_for_account(CARD)
        if t.status is TransactionStatus.FOLDED
    )


def counted(store: Store, description: str) -> bool:
    return any(
        t.description == description and not t.status.is_history
        for t in store.transactions_for_account(CARD)
    )


def charge_rows(*positions: int) -> list[str]:
    return sorted(f"Plan Charge {position}" for position in positions)


def chain(store: Store) -> dict[date, bool]:
    """Each between-closings period's closing day -> whether it agrees."""
    [item] = period_reconciliation(store, sibling_accounts={}).accounts
    return {p.last_day: p.agrees for p in item.periods if p.kind is not PeriodKind.INSIDE}


def surpluses(store: Store) -> dict[date, int]:
    [item] = period_reconciliation(store, sibling_accounts={}).accounts
    return {p.last_day: p.surplus_minor for p in item.periods if p.kind is not PeriodKind.INSIDE}


class TestTheNineStatementCard:
    def test_Corpus_BeforeTheFold_EveryPeriodAfterTheFirstIsOverByItsOwnChargeRow(
        self, store, tmp_path
    ):
        """The fault the fold exists for, measured on the corpus: a first period
        that agrees, and eight after it each over by exactly the feed's charge
        row dated its first day (the store's sign makes a charge negative)."""
        build_card(store, tmp_path)

        held = chain(store)
        over = surpluses(store)

        assert held[CLOSINGS[0]]
        assert [held[closing] for closing in CLOSINGS[1:]] == [False] * 8
        assert [over[closing] for closing in CLOSINGS[1:]] == [-c for c in CHARGES[1:]]

    def test_Fold_OnTheNineStatementCard_FoldsTheFeedRowDatedEachPeriodsFirstDay(
        self, store, tmp_path
    ):
        """Eight feed rows, one per held statement after the first, including the
        one dated 12 March, which is the April statement's charge in a period
        that spans the missing statement."""
        build_card(store, tmp_path)

        report = fold_same_money(store)

        assert report.folded == 8
        assert folded(store) == charge_rows(*range(1, 9))

    def test_Fold_OnTheNineStatementCard_LeavesWhatNoHeldStatementAnswersForCounted(
        self, store, tmp_path
    ):
        """Dated 12 February is the charge of the March statement, which is not
        held: nothing but the feed records it, so it is real spending. Dated
        11 July is the charge of an August statement not yet held. The first
        period's feed row is pending, so never counted and never folded. The
        thirty ordinary rows of the missing month are the only record there is."""
        build_card(store, tmp_path)

        fold_same_money(store)

        assert counted(store, "Plan Charge Missing")
        assert counted(store, "Plan Charge After")
        assert sum(counted(store, f"Gap Purchase {n}") for n in range(30)) == 30
        [first] = [
            t for t in store.transactions_for_account(CARD) if t.description == "Plan Charge 0"
        ]
        assert first.status is TransactionStatus.PENDING

    def test_Fold_OnTheNineStatementCard_MakesEveryPeriodAgreeAndLeavesTheStatementRowsCounted(
        self, store, tmp_path
    ):
        build_card(store, tmp_path)

        fold_same_money(store)

        assert all(chain(store).values())
        parts = [
            t for t in store.transactions_for_account(CARD) if t.description.startswith("Plan Part")
        ]
        assert len(parts) == 24
        assert all(t.status is TransactionStatus.BOOKED for t in parts)

    def test_Fold_RunTwiceOnTheNineStatementCard_ChangesNothingTheSecondTime(
        self, store, tmp_path
    ):
        build_card(store, tmp_path)
        fold_same_money(store)

        again = fold_same_money(store)

        assert (again.folded, again.newly_folded, again.released) == (8, 0, 0)

    def test_Fold_WhenTheLastStatementIsMissing_FoldsTheSevenBeforeItAndNotTheOneAfter(
        self, store, tmp_path
    ):
        """Without the July statement nothing says the 11 June charge is the
        same money as anything."""
        build_card(store, tmp_path, skip=frozenset({8}))

        fold_same_money(store)

        assert folded(store) == charge_rows(*range(1, 8))
        assert counted(store, "Plan Charge 8")

    def test_Fold_WhenAnAugustStatementIsLaterHeld_FoldsTheChargeDatedElevenJuly(
        self, store, tmp_path
    ):
        """The row dated 11 July stays counted until a statement closing in
        August lists the charge it duplicates. The feed must carry something
        later than that closing for the two sources to be compared over it."""
        card = build_card(store, tmp_path)
        fold_same_money(store)
        assert counted(store, "Plan Charge After")
        land(store, feed_row(1357, date(2026, 8, 20), "Later Purchase"))
        _held_statement(
            store,
            tmp_path,
            "august",
            statement_day(date(2026, 8, 10)),
            card.owed,
            [
                ("10th Aug", "August Part A", AUGUST_CHARGE - 311),
                ("10th Aug", "August Part B", 311),
            ],
        )

        fold_same_money(store)

        assert "Plan Charge After" in folded(store)
        assert all(chain(store).values())


class TestWhatIsNotTheSameMoney:
    def test_Fold_WhenAGenuineRowOfAnotherAmountSitsInOnePeriod_LeavesThatPeriodsChargeAlone(
        self, store, tmp_path
    ):
        """A 3.33 purchase only the feed holds, dated 1 June inside the period
        closing 10 June. That period differs by the charge AND the purchase, a
        figure no statement row sums to, so its charge stays counted beside the
        purchase and the period is reported as differing. Every other period is
        judged on its own and folds."""
        build_card(
            store, tmp_path, extra_feed=[feed_row(333, date(2026, 6, 1), "Stray Purchase")]
        )

        fold_same_money(store)

        assert folded(store) == charge_rows(1, 2, 3, 4, 5, 6, 8)
        assert counted(store, "Stray Purchase")
        assert counted(store, "Plan Charge 7")
        held = chain(store)
        assert held[CLOSINGS[7]] is False
        assert [held[closing] for closing in CLOSINGS if closing != CLOSINGS[7]] == [True] * 8

    def test_Fold_WhenAGenuineRowFollowsTheLastClosing_FoldsOnlyTheCharges(self, store, tmp_path):
        """A purchase dated 11 July, beside the charge dated the same day: no
        statement lists it and no period holds it, so it is not the charge and
        stays counted."""
        build_card(
            store, tmp_path, extra_feed=[feed_row(1234, date(2026, 7, 11), "Genuine After")]
        )

        fold_same_money(store)

        assert folded(store) == charge_rows(*range(1, 9))
        assert counted(store, "Genuine After")

    def test_Fold_WhenAGenuineRowSitsInAMiddlePeriod_FoldsTheOtherPeriodsOnly(
        self, store, tmp_path
    ):
        """A purchase dated 14 January sits in the period closing 11 February,
        which then differs by it as well as the charge: that period's charge
        stays counted and the seven others fold."""
        build_card(
            store, tmp_path, extra_feed=[feed_row(1234, date(2026, 1, 14), "Genuine Middle")]
        )

        fold_same_money(store)

        assert folded(store) == charge_rows(1, 2, 3, 5, 6, 7, 8)
        assert counted(store, "Genuine Middle")
        assert counted(store, "Plan Charge 4")

    def test_Fold_WhenTheChargeAmountIsAPennyOff_FoldsNothingForThatStatement(
        self, store, tmp_path
    ):
        """The feed's 3 charge is a penny more than the statement's: the period
        differs by a figure no statement row sums to, so that period folds nothing
        and the rest fold."""
        build_card(store, tmp_path, feed_charges={3: CHARGES[3] + 1})

        fold_same_money(store)

        assert folded(store) == charge_rows(1, 2, 4, 5, 6, 7, 8)
        assert counted(store, "Plan Charge 3")
        assert chain(store)[CLOSINGS[3]] is False

    def test_Fold_WhenTheFeedDatesTheChargeAfterTheClosing_FoldsNothingForThatStatement(
        self, store, tmp_path
    ):
        """The feed dates the January statement's charge on 13 January, the day
        after its closing. By date it is the next period's first day, where the
        statement lists a different charge, so it is not folded: the rule looks
        for a statement's charge only in that statement's own period."""
        build_card(store, tmp_path, charge_days={3: date(2026, 1, 13)})

        fold_same_money(store)

        assert "Plan Charge 3" not in folded(store)
        assert counted(store, "Plan Charge 3")

    def test_Fold_WhenAStatementsChargeIsDatedLaterInItsOwnPeriod_StillFoldsIt(
        self, store, tmp_path
    ):
        """The feed may date the charge any day of the period, not only the
        first: the period's difference picks the row. (A date within a week of
        the statement's row is merged into it by identity, so the date is 20
        December, three weeks before the closing.)"""
        build_card(store, tmp_path, charge_days={3: date(2025, 12, 20)})

        fold_same_money(store)

        assert folded(store) == charge_rows(*range(1, 9))


class TestWhichOfEqualRowsIsHidden:
    def test_Fold_WhenTheMissingStatementsChargeEqualsTheHeldOne_HidesTheLaterRow(
        self, store, tmp_path
    ):
        """Dated 12 February (the March statement's, not held) and dated 12 March
        (the April statement's) are one amount, and either makes the period
        agree. The later is folded: the held statement's charge sits nearest
        its own rows. The period's total is right either way; only which row
        stays visible, and on what date, differs."""
        build_card(store, tmp_path, missing_charge=CHARGES[GAP_POSITION])

        fold_same_money(store)

        assert folded(store) == charge_rows(*range(1, 9))
        assert counted(store, "Plan Charge Missing")
        assert all(chain(store).values())

    def test_Fold_WhenEveryChargeIsOneAmount_FoldsNothingAndEveryPeriodAgrees(
        self, store, tmp_path
    ):
        """PREDICTED eight folds; MEASURED none. The identity layer already
        merges each feed row with a statement row of the same amount a day
        away, so no period is over."""
        build_card(store, tmp_path, equal_charges=True)

        report = fold_same_money(store)

        assert report.folded == 0
        assert all(chain(store).values())


class TestWhatIsNeverFolded:
    def test_Fold_WhenTheChargeRowIsAConfirmedTransferLeg_LeavesItCounted(self, store, tmp_path):
        """The leg stays counted, so its period stays over by it: that period is
        reported as differing and every other folds."""
        build_card(store, tmp_path, transfer=False)
        pair_with_savings(store, "Plan Charge 3")

        fold_same_money(store)

        assert folded(store) == charge_rows(1, 2, 4, 5, 6, 7, 8)
        assert counted(store, "Plan Charge 3")
        assert chain(store)[CLOSINGS[3]] is False

    def test_Fold_WhenAStatementListsTheChargeRow_LeavesItCounted(self, store, tmp_path):
        """A feed-only leftover that a statement also lists (here simulated by
        placing it with the next statement's membership) is the statement's own
        row, whatever the pairing made of its dates."""
        build_card(store, tmp_path)
        [item] = gather_evidence(store, sibling_accounts={})
        [listed] = [t for t in item.counted if t.description == "Plan Charge 3"]
        placed = {**item.membership.placed, listed.entity_id: CLOSINGS[3]}
        listed_item = replace(item, membership=Membership(item.membership.statements, placed))

        plan = plan_same_money([listed_item])

        assert listed.entity_id not in plan.folds

    def test_Fold_NeverUsesAnotherAccountsFeedRow(self, store, tmp_path):
        """The only row of a charge's amount in the period is another account's:
        it is not folded, and the card's own charge is."""
        build_card(store, tmp_path)
        land(
            store,
            feed_row(CHARGES[3], date(2025, 12, 20), "Other Card Row", account="other-card"),
            digest="other",
        )

        fold_same_money(store)

        assert [t.status for t in store.transactions_for_account("other-card")] == [
            TransactionStatus.BOOKED
        ]
        assert folded(store) == charge_rows(*range(1, 9))


class TestAFirstStatementWhoseOpeningIsUnknown:
    def test_Plan_WhenTheFirstStatementsPeriodCannotBeBuilt_FoldsTheOthersAndTestsOneFewer(
        self, store, tmp_path
    ):
        """No window is built for the first statement without its printed
        opening, so its period is not tested; the periods between later
        closings are, and fold as before."""
        build_card(store, tmp_path)
        [item] = gather_evidence(store, sibling_accounts={})
        without = replace(
            item, windows=[w for w in item.windows if w.kind is not PeriodKind.FIRST]
        )

        plan = plan_same_money([without])

        [account] = plan.outcomes
        assert len(plan.folds) == 8
        assert [c.closing for c in account.closings] == CLOSINGS[1:]


class TestASingleStatement:
    def test_Fold_WhenOnlyOneStatementIsHeld_FoldsNothingAndDoesNotFail(self, store, tmp_path):
        build_card(store, tmp_path, skip=frozenset(range(1, 9)))

        report = fold_same_money(store)

        assert report.folded == 0
        assert folded(store) == []


class TestAStatementLaterListingTheRow:
    def test_Fold_WhenALaterStatementListsAFoldedChargeRow_ReleasesThatRowOnly(
        self, store, tmp_path
    ):
        """After the eight are folded, a statement closing 10 August prints the
        December charge among its rows (the statement's own row, however it was
        dated). That row is no longer 'the same money described differently';
        the other seven stay folded. Importing the statement runs the fold
        itself, and running it again changes nothing."""
        card = build_card(store, tmp_path)
        fold_same_money(store)
        assert len(folded(store)) == 8
        self._hold_listing(card, tmp_path)

        again = fold_same_money(store)

        assert folded(store) == charge_rows(1, 2, 4, 5, 6, 7, 8)
        assert counted(store, "Plan Charge 3")
        assert (again.newly_folded, again.released) == (0, 0)

    @staticmethod
    def _hold_listing(card: Card, tmp_path: Path) -> None:
        _held_statement(
            card.store,
            tmp_path,
            "later",
            statement_day(date(2026, 8, 10)),
            card.owed,
            [(text_day(charge_day_of(3)), "Plan Charge 3", CHARGES[3])],
        )
