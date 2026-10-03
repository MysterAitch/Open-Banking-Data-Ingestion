"""The same-money fold on a card held over nine statements with a month missing.

The corpus and every answer are in `card_chain_corpus`. This module asks the
questions: which feed rows are folded, which stay counted, and whether every
period then agrees where the answer says it can. The shape is the one a real
card showed (the fold, as first written, folded nothing on it).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from card_chain_corpus import (
    CARD,
    CHARGE,
    CLOSINGS,
    Card,
    build_card,
    feed_day_of,
    feed_row,
    land,
    statement_day,
    text_day,
)
from obdi.models import TransactionStatus
from obdi.period_reconciliation import PeriodKind, period_reconciliation
from obdi.same_money_fold import fold_same_money
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


class TestTheNineStatementCard:
    def test_Corpus_BeforeTheFold_EveryPeriodAfterTheFirstDiffersByTheCharge(self, store, tmp_path):
        """The fault the fold exists for, measured on the corpus: a first period
        that agrees, and eight after it each over by one charge."""
        build_card(store, tmp_path)

        held = chain(store)

        assert held[CLOSINGS[0]]
        assert [held[closing] for closing in CLOSINGS[1:]] == [False] * 8

    def test_Fold_WhenTheFirstTwoPeriodsHoldRowsTheFeedNeverSaw_FoldsTheChargeOfEveryHeldStatement(
        self, store, tmp_path
    ):
        """Nine feed rows, one per held statement, the last dated after the final
        closing. The first period has a statement row the feed never saw, the
        second has another of a different amount: neither stops the others."""
        build_card(store, tmp_path)

        report = fold_same_money(store)

        assert report.folded == 9
        assert folded(store) == charge_rows(*range(9))

    def test_Fold_OnTheNineStatementCard_LeavesTheMissingStatementsChargeAndOrdinaryRowsCounted(
        self, store, tmp_path
    ):
        """The charge dated 12 March belongs to the statement that is not held:
        nothing but the feed records it, so it is real spending. The thirty
        ordinary feed rows of that month are likewise the only record there is."""
        build_card(store, tmp_path)

        fold_same_money(store)

        assert counted(store, "Plan Charge Missing")
        assert sum(counted(store, f"Gap Purchase {n}") for n in range(30)) == 30

    def test_Fold_OnTheNineStatementCard_MakesEveryPeriodAgree(self, store, tmp_path):
        build_card(store, tmp_path)

        fold_same_money(store)

        assert all(chain(store).values())

    def test_Fold_RunTwiceOnTheNineStatementCard_ChangesNothingTheSecondTime(
        self, store, tmp_path
    ):
        build_card(store, tmp_path)
        fold_same_money(store)

        again = fold_same_money(store)

        assert (again.folded, again.newly_folded, again.released) == (9, 0, 0)

    def test_Fold_WhenTheLastStatementIsMissing_FoldsTheEightBeforeItAndNotTheOneAfter(
        self, store, tmp_path
    ):
        """Without the July statement nothing says the 11 July charge is the
        same money as anything."""
        build_card(store, tmp_path, skip=frozenset({8}))

        fold_same_money(store)

        assert folded(store) == charge_rows(*range(8))


class TestWhereTheChargeLandsRelativeToTheClosing:
    @pytest.mark.parametrize(
        ("offset", "expected"),
        [(0, 9), (1, 9), (2, 9), (3, 0)],
        ids=["on-the-closing-day", "day-after", "two-days-after", "beyond-the-boundary"],
    )
    def test_Fold_WhenTheFeedDatesTheChargeAtAnOffset_FoldsOnlyWithinTheBoundary(
        self, store, tmp_path, offset, expected
    ):
        build_card(store, tmp_path, charge_offset=offset)

        report = fold_same_money(store)

        assert report.folded == expected


class TestWhatIsNotTheSameMoney:
    def test_Fold_WhenAGenuineRowOfAnotherAmountSitsInOnePeriod_LeavesItCounted(
        self, store, tmp_path
    ):
        """A 3.33 purchase only the feed holds, dated 1 June inside the period
        closing 10 June. That period then differs for a reason that is not the
        charge, so the folds that touch it are refused: the charge dated into it
        (13 May) and its own (11 June), and the one after, whose period then
        holds the unfolded 11 June row. Every earlier fold stands and every
        earlier period agrees."""
        build_card(
            store, tmp_path, extra_feed=[feed_row(333, date(2026, 6, 1), "Stray Purchase")]
        )

        fold_same_money(store)

        assert folded(store) == charge_rows(0, 1, 2, 3, 4, 5)
        assert counted(store, "Stray Purchase")
        held = chain(store)
        assert [held[closing] for closing in CLOSINGS[:7]] == [True] * 7
        assert [held[closing] for closing in CLOSINGS[7:]] == [False, False]

    def test_Fold_WhenAStrangerRowOfTheChargesAmountSitsOnAClosingDay_LeavesBothCounted(
        self, store, tmp_path
    ):
        """A genuine 6.95 purchase on 10 June, the closing day, beside the
        charge dated 11 June. The charge posts on or after the closing, so it is
        the one tried; the period holding the stranger still differs, so the
        fold is refused. The stranger is not folded in the charge's place, which
        would leave the charge itself double counted in the next period. Nothing
        is hidden: the period is reported as differing."""
        build_card(
            store,
            tmp_path,
            extra_feed=[feed_row(CHARGE, date(2026, 6, 10), "Stranger Of The Same Size")],
        )

        fold_same_money(store)

        assert counted(store, "Stranger Of The Same Size")
        assert "Plan Charge 7" not in folded(store)
        assert chain(store)[CLOSINGS[7]] is False

    def test_Fold_WhenAGenuineRowFollowsTheLastClosing_FoldsOnlyTheCharge(self, store, tmp_path):
        """A purchase dated 11 July, beside the charge of the same day: no
        statement lists it and no period holds it, so it is not the charge and
        stays counted while the charge folds."""
        build_card(
            store, tmp_path, extra_feed=[feed_row(1234, date(2026, 7, 11), "Genuine After")]
        )

        fold_same_money(store)

        assert folded(store) == charge_rows(*range(9))
        assert counted(store, "Genuine After")

    def test_Fold_WhenAGenuineRowFollowsAMiddleClosing_FoldsTheEarlierChargesOnly(
        self, store, tmp_path
    ):
        """A purchase dated 13 January, the day after the closing of 12 January,
        sits in the next period with no statement row to answer it. That period
        differs, so folds that touch it or depend on it are refused; the three
        before it stand."""
        build_card(
            store, tmp_path, extra_feed=[feed_row(1234, date(2026, 1, 13), "Genuine Middle")]
        )

        fold_same_money(store)

        assert folded(store) == charge_rows(0, 1, 2)
        assert counted(store, "Genuine Middle")

    def test_Fold_WhenTheChargeAmountIsAPennyOff_FoldsNothingForThatStatement(
        self, store, tmp_path
    ):
        build_card(store, tmp_path, feed_charges={3: CHARGE + 1})

        fold_same_money(store)

        assert "Plan Charge 3" not in folded(store)
        assert counted(store, "Plan Charge 3")


class TestASingleStatement:
    def test_Fold_WhenOnlyOneStatementIsHeld_FoldsNothingAndDoesNotFail(self, store, tmp_path):
        build_card(store, tmp_path, skip=frozenset(range(1, 9)))

        report = fold_same_money(store)

        assert report.folded == 0
        assert folded(store) == []


class TestStatementsClosingWithinTheBoundaryOfEachOther:
    def _card(self, store: Store, tmp_path: Path, first_charge: int, second_charge: int):
        _held_statement(
            store, tmp_path, "s0", "10th May 2026", 10000, [("1st May", "Plain Purchase", 2231)]
        )
        _held_statement(
            store,
            tmp_path,
            "s1",
            statement_day(date(2026, 6, 10)),
            12231,
            [("10th Jun", "Part A", first_charge - 111), ("10th Jun", "Part B", 111)],
        )
        _held_statement(
            store,
            tmp_path,
            "s2",
            statement_day(date(2026, 6, 11)),
            12231 + first_charge,
            [("11th Jun", "Part C", second_charge - 222), ("11th Jun", "Part D", 222)],
        )
        land(
            store,
            feed_row(2231, date(2026, 5, 1), "Plain Purchase"),
            feed_row(first_charge, date(2026, 6, 11), "Combined One"),
            feed_row(second_charge, date(2026, 6, 12), "Combined Two"),
        )

    def test_Fold_WhenTwoClosingsAreOneDayApartAndTheChargesDiffer_FoldsEachFeedRowOnce(
        self, store, tmp_path
    ):
        """Both feed rows are inside both closings' bands. The earlier statement
        takes the 11th, the later the 12th, and each row is claimed once."""
        self._card(store, tmp_path, 500, 900)

        fold_same_money(store)

        assert folded(store) == ["Combined One", "Combined Two"]

    def test_Fold_WhenTwoClosingsAreOneDayApartAndTheChargesAreEqual_FoldsBothRows(
        self, store, tmp_path
    ):
        self._card(store, tmp_path, 700, 700)

        fold_same_money(store)

        assert folded(store) == ["Combined One", "Combined Two"]


class TestAStatementLaterListingTheRow:
    def test_Fold_WhenALaterStatementListsAFoldedChargeRow_ReleasesThatRowOnly(
        self, store, tmp_path
    ):
        """After the nine are folded, a statement closing 10 August prints the
        11 January charge among its rows (the statement's own row, however it
        was dated). That row is no longer 'the same money described
        differently'; the other eight stay folded. Importing the statement
        runs the fold itself, and running it again changes nothing."""
        card = build_card(store, tmp_path)
        fold_same_money(store)
        assert len(folded(store)) == 9
        self._hold_listing(card, tmp_path)

        again = fold_same_money(store)

        assert folded(store) == charge_rows(0, 1, 2, 4, 5, 6, 7, 8)
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
            [(text_day(feed_day_of(3)), "Plan Charge 3", CHARGE)],
        )
