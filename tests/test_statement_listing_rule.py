"""The rule that tests a statement by what it LISTS (`agreement`, R1 to R5), on constructed
known balances whose answers were decided before the first run.

Every account is described the way `agreement` reads it: the known balances with how far each is
from what the transactions predict from the one opening (`Known.distance`), and what the
measurement concluded about each statement (`StatementCheck`). Invented figures throughout.

The running example is a current account. A statement closes on 28 February, its balance 1,300.00,
opening from 1,000.00 on 31 January and listing 300.00 of money in. A transaction of 500.00 in,
dated 28 February, is listed by nobody. The bank's balance for 28 February is 1,800.00. So:

    the transactions predict 1,800.00 at the end of 28 February (1,000 + 300 + 500);
    the statement's own balance is 500.00 short of that, and the bank's is not.

Taken as the statement having closed before the 500.00, the two do not conflict.
"""

from __future__ import annotations

from datetime import date

from obdi.agreement import (
    AGREES,
    DEFINES,
    HELD_CONFLICT,
    HELD_MOVEMENT,
    HELD_STATEMENT,
    HELD_UNMET,
    MET,
    NONE,
    UNMET,
    UNTESTED,
    Fault,
    Known,
    Standing,
    derive_agreement,
    statement_fault_sentence,
)
from obdi.balance_anchors import BANK, STATEMENT
from obdi.standing_data import (
    ADDS_UP,
    DOES_NOT_ADD_UP,
    NOTHING_TO_CHECK_AGAINST,
    AccountStanding,
    verification_of,
)
from obdi.statement_checks import (
    NOT_HELD,
    ClosedBefore,
    StatementCheck,
    StatementChecks,
)

D = date


def closing(day: date, figure: int, distance: int, *, defines: bool = False) -> Known:
    """A statement's closing balance as the chain reads it."""
    return Known(
        day,
        "statement-source",
        DEFINES if defines else (MET if distance == 0 else UNMET),
        figure,
        basis=STATEMENT,
        distance=distance,
    )


def bank(day: date, figure: int, distance: int) -> Known:
    return Known(
        day,
        "bank-source",
        MET if distance == 0 else UNMET,
        figure,
        basis=BANK,
        distance=distance,
    )


def check(
    day: date,
    figure: int,
    *,
    adds_up: bool | None = True,
    days_tested: bool = True,
    listed: int = 3,
    fault: str = "",
    closed_before: ClosedBefore | None = None,
    unlisted: int | None = 0,
) -> StatementCheck:
    return StatementCheck(day, figure, listed, adds_up, days_tested, unlisted, fault, closed_before)


def standing_of_agreement(agreement) -> str:
    return verification_of(AccountStanding(Standing(agreement, None), None, False))


def derive(known, checks=(), faults=()):
    return derive_agreement(known, faults, checks=StatementChecks(tuple(checks)))


JAN31, FEB28, MAR31 = D(2026, 1, 31), D(2026, 2, 28), D(2026, 3, 31)


class TestALoneStatement:
    def test_Account_WhenItsOnlyKnownBalanceIsAListingThatAddsUp_AddsUpThroughItsClosing(
        self,
    ):
        known = [closing(FEB28, 1300, 0, defines=True)]

        before = derive_agreement(known, [])
        after = derive(known, [check(FEB28, 1300)])

        assert (before.state, before.through) == (UNTESTED, None)
        assert (after.state, after.through) == (AGREES, FEB28)
        assert after.tested == (FEB28,)
        assert standing_of_agreement(after) == ADDS_UP

    def test_Account_WhenTheStatementsDaysHoldTransactionsNoStatementLists_StaysUntested(self):
        known = [closing(FEB28, 1300, 0, defines=True)]

        after = derive(known, [check(FEB28, 1300, days_tested=False, unlisted=2)])

        assert (after.state, after.through) == (UNTESTED, None)
        assert standing_of_agreement(after) == NOTHING_TO_CHECK_AGAINST

    def test_Account_WhenTheStatementCannotSayWhetherItAddsUp_NothingIsVerifiedOrFaulted(self):
        known = [closing(FEB28, 1300, 0, defines=True)]

        after = derive(known, [check(FEB28, 1300, adds_up=None)])

        assert (after.state, after.through, after.held) == (UNTESTED, None, None)
        assert standing_of_agreement(after) == NOTHING_TO_CHECK_AGAINST

    def test_Account_WhenTheStatementStatesNoOpeningBalance_StaysNothingToCheckAgainst(self):
        known = [closing(FEB28, 1300, 0, defines=True)]

        after = derive(known, [check(FEB28, 1300, adds_up=None, days_tested=False)])

        assert standing_of_agreement(after) == NOTHING_TO_CHECK_AGAINST

    def test_Account_WhenTheStatementIsAFault_DoesNotAddUpAndNamesTheStatement(self):
        known = [closing(FEB28, 1300, 0, defines=True)]
        wrong = check(FEB28, 1300, adds_up=False, days_tested=False, fault=NOT_HELD)

        after = derive(known, [wrong])

        assert (after.state, after.through) == (HELD_STATEMENT, None)
        assert after.held is not None and after.held.day == FEB28
        assert standing_of_agreement(after) == DOES_NOT_ADD_UP
        said = statement_fault_sentence(wrong)
        assert "2026-02-28" in said and "not held" in said

    def test_Account_WhenAnUntrustedStatementIsAFaultAndNoBalanceIsKnown_StillDoesNotAddUp(self):
        wrong = check(FEB28, 1300, adds_up=False, days_tested=False, fault=NOT_HELD)

        after = derive([], [wrong])

        assert after.state == HELD_STATEMENT
        assert standing_of_agreement(after) == DOES_NOT_ADD_UP


class TestAMovementFaultInsideAStatementThatAddsUp:
    def test_Account_WhenAMovementCheckFaultsADayInsideIt_NothingIsVerifiedThroughItsClosing(
        self,
    ):
        known = [closing(FEB28, 1300, 0, defines=True)]
        inside = Fault(D(2026, 2, 10), "a payment lists a row the store does not hold")

        after = derive(known, [check(FEB28, 1300)], [inside])

        assert (after.state, after.through) == (HELD_MOVEMENT, None)
        assert after.held is not None and after.held.day == D(2026, 2, 10)
        assert standing_of_agreement(after) == DOES_NOT_ADD_UP

    def test_Account_WhenTheMovementFaultIsDatedAfterTheClosing_TheStatementStillAddsUp(self):
        known = [closing(FEB28, 1300, 0, defines=True)]
        later = Fault(D(2026, 3, 5), "a payment lists a row the store does not hold")

        after = derive(known, [check(FEB28, 1300)], [later])

        assert (after.state, after.through, after.held) == (AGREES, FEB28, None)


class TestAStatementThatIsAFaultAmongOthers:
    def test_Account_WhenALaterStatementIsAFault_AddsUpOnlyThroughTheOnesBeforeIt(self):
        known = [
            closing(JAN31, 1000, 0, defines=True),
            closing(FEB28, 1300, 0),
            closing(MAR31, 1500, 0),
        ]
        wrong = check(MAR31, 1500, adds_up=False, days_tested=False, fault=NOT_HELD)

        after = derive(known, [check(JAN31, 1000), check(FEB28, 1300), wrong])

        assert (after.state, after.through) == (HELD_STATEMENT, FEB28)
        assert after.held is not None and after.held.day == MAR31


class TestAStatementClosedBeforeTheDaysLastTransaction:
    EXPLAINED = ClosedBefore(FEB28, 1, False, (500,), (), (500,))

    def known(self):
        return [
            closing(JAN31, 1000, 0, defines=True),
            closing(FEB28, 1300, -500),
            bank(FEB28, 1800, 0),
        ]

    def test_Account_WhenTheTwoDifferByExactlyTheUnlistedTransactionOfThatDay_NoConflictAndAddsUp(
        self,
    ):
        today = derive_agreement(self.known(), [])
        after = derive(self.known(), [check(FEB28, 1300, closed_before=self.EXPLAINED)])

        assert today.state == HELD_CONFLICT
        assert (after.state, after.through, after.conflicts) == (AGREES, FEB28, ())
        assert standing_of_agreement(after) == ADDS_UP
        assert [c.day for c in after.closed_before] == [FEB28]

    def test_Account_WhenNothingExplainsTheDifference_StaysAConflict(self):
        after = derive(self.known(), [check(FEB28, 1300)])

        assert after.state == HELD_CONFLICT

    def test_Account_WhenTheClaimedDifferenceIsNotTheFiguresActualDifference_StaysAConflict(self):
        wrong = ClosedBefore(FEB28, 1, False, (499,), (), (499,))

        after = derive(self.known(), [check(FEB28, 1300, closed_before=wrong)])

        assert after.state == HELD_CONFLICT

    def test_Account_WhenALaterBalanceIsReproducedAfterIt_ChainContinuesThroughThatDay(self):
        known = [*self.known(), bank(MAR31, 1900, 0)]

        after = derive(known, [check(FEB28, 1300, closed_before=self.EXPLAINED)])

        assert (after.state, after.through) == (AGREES, MAR31)

    def test_Account_WhenALaterBalanceIsNotReproducedAfterIt_AddsUpOnlyThroughTheExplainedDay(
        self,
    ):
        known = [*self.known(), bank(MAR31, 1907, 7)]

        after = derive(known, [check(FEB28, 1300, closed_before=self.EXPLAINED)])

        assert (after.state, after.through) == (HELD_UNMET, FEB28)
        assert after.held is not None and after.held.day == MAR31

    def test_Account_WhenTheStatementDefinesTheOpening_TheOtherBalancesAreJudgedWithTheTransaction(
        self,
    ):
        # The statement is the earliest known balance, so the opening is derived from it with the
        # 500.00 counted and the bank's balance and the later one both stand 500.00 away.
        known = [
            closing(FEB28, 1300, 0, defines=True),
            bank(FEB28, 1800, 500),
            bank(MAR31, 1900, 500),
        ]

        after = derive(known, [check(FEB28, 1300, closed_before=self.EXPLAINED)])

        assert (after.state, after.through, after.conflicts) == (AGREES, MAR31, ())

    def test_Account_WhenTheTransactionIsDatedTheNextDay_TheStatementIsNotShiftedAndNoConflict(
        self,
    ):
        known = [closing(FEB28, 1300, 0, defines=True), bank(FEB28, 1800, 0)]
        next_day = ClosedBefore(FEB28, 1, True, (), (500,), ())

        after = derive(known, [check(FEB28, 1300, closed_before=next_day)])

        assert (after.state, after.conflicts) == (AGREES, ())

    def test_Account_WhenThePendingExplanationIsAlreadyPlacedAfterTheClosing_NothingIsShiftedTwice(
        self,
    ):
        # A later statement lists the transaction, so the chain already leaves it out of this
        # statement's closing: the statement is MET and the shift is nil.
        known = [
            closing(JAN31, 1000, 0, defines=True),
            closing(FEB28, 1300, 0),
            bank(FEB28, 1800, 0),
        ]
        listed_later = ClosedBefore(FEB28, 1, False, (500,), (), ())

        after = derive(known, [check(FEB28, 1300, closed_before=listed_later)])

        assert (after.state, after.through, after.conflicts) == (AGREES, FEB28, ())


class TestTheTwoHypothesesAndTheirLimits:
    """A statement that closed at some moment precedes EVERYTHING unlisted after it: the other
    balance is for the end of the closing day, or was taken the day after, and nothing else."""

    def typed_defines(self, figure: int) -> Known:
        return Known(
            FEB28, "typed", DEFINES, figure, basis="stated", distance=0
        )

    def test_Account_WhenTheOtherBalanceWasTakenTheDayAfter_BothDaysTransactionsExplainIt(self):
        # S 1300, B 800; one unlisted of -300 dated 28 Feb and one of -200 dated 1 Mar. B is
        # for the day after, so it includes both; S, which closed before them, does not.
        known = [self.typed_defines(800), closing(FEB28, 1300, 500)]
        claim = ClosedBefore(FEB28, 2, True, (-300,), (-200,), (-300,))

        after = derive(known, [check(FEB28, 1300, closed_before=claim)])

        assert (after.state, after.conflicts) == (AGREES, ())
        assert [c.next_day for c in after.closed_before] == [True]

    def test_Account_WhenOnlyTheNextDaysTransactionEqualsTheDifference_StaysAConflict(self):
        # The same day also holds one of -123 that is left out: a subset fitted to the figure.
        known = [self.typed_defines(523), closing(FEB28, 1300, 777)]
        subset = ClosedBefore(FEB28, 1, True, (-123,), (-777,), (-123,))

        after = derive(known, [check(FEB28, 1300, closed_before=subset)])

        assert after.state == HELD_CONFLICT

    def test_Account_WhenTheStatementCannotSay_ItExplainsNothingAway(self):
        known = [
            closing(JAN31, 1000, 0, defines=True),
            closing(FEB28, 1300, -500),
            bank(FEB28, 1800, 0),
        ]
        explained = ClosedBefore(FEB28, 1, False, (500,), (), (500,))

        after = derive(
            known,
            [check(FEB28, 1300, adds_up=None, days_tested=False, closed_before=explained)],
        )

        assert after.state == HELD_CONFLICT

    def test_Account_WhenTwoDocumentsCloseTheDay_NeitherTestsItsDaysOrExplainsAnything(self):
        known = [closing(FEB28, 1300, 0, defines=True)]
        clash = StatementCheck(FEB28, 1300, 3, True, True, 0, clash=True)

        after = derive(known, [clash])

        assert (after.state, after.through) == (UNTESTED, None)


class TestAnAccountThatAddsUpToday:
    def test_Account_WhenTheChainAlreadyAddsUp_TheChecksChangeNothingButTheStatementFault(self):
        known = [
            closing(JAN31, 1000, 0, defines=True),
            closing(FEB28, 1300, 0),
            closing(MAR31, 1500, 0),
        ]
        today = derive_agreement(known, [])

        same = derive(known, [check(JAN31, 1000), check(FEB28, 1300), check(MAR31, 1500)])

        assert (same.state, same.through, same.known_to) == (today.state, today.through, MAR31)
        assert today.tested == (FEB28, MAR31)
        assert same.tested == (JAN31, FEB28, MAR31)

    def test_Account_WhenNoCheckIsKnown_IsExactlyTodaysAnswer(self):
        known = [closing(JAN31, 1000, 0, defines=True), closing(FEB28, 1300, -4)]

        assert derive_agreement(known, []) == derive_agreement(
            known, [], checks=StatementChecks(())
        )

    def test_Account_WhenNothingIsKnown_StaysNone(self):
        assert derive([], []).state == NONE
