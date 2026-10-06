"""The things to do, as one kind of object, for an ordinary day, a bad day, and a clear one.

Every household is invented and every count and order below was decided with it, before `todo`
was run. The gaps and the attention items are built directly, because what is under test is how
they are gathered, not how they are found (`test_fetch_gaps`, `test_overview`).
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from obdi.agreement import AGREES, Agreement, Known, Standing
from obdi.fetch_gaps import AccountOutlook, Basis, FetchGap, FetchReport, GapKind
from obdi.overview import (
    HOUSEKEEPING,
    INFORMATION,
    NOW,
    SOON,
    AttentionItem,
    Overview,
)
from obdi.page_times import date_with_age
from obdi.standing_data import AccountStanding
from obdi.todo import (
    Todo,
    build_todos,
    lockable,
)

TODAY = date(2026, 10, 5)
NAMES = {
    "everyday": "Everyday card",
    "joint": "Joint current",
    "rainy": "Rainy day saver",
    "holiday": "Holiday pot",
}


def label_of(ref: str) -> str:
    return NAMES.get(ref, ref)


def d(text: str) -> date:
    return date.fromisoformat(text)


def overview(*items: AttentionItem) -> Overview:
    return Overview(
        generated_at=datetime(2026, 10, 5, 8, 12, tzinfo=UTC),
        checks_total=19,
        checks_run=19,
        items=items,
        accounts=(),
    )


def gap(
    account: str,
    kind: GapKind,
    first: str,
    last: str,
    *,
    basis: Basis = Basis.STATED,
    closings: tuple[str, ...] = (),
    rows_to: str | None = None,
) -> FetchGap:
    return FetchGap(
        account,
        kind,
        d(first),
        d(last),
        basis,
        "",
        "a reason",
        probably=len(closings) or None,
        closings=tuple(d(c) for c in closings),
        rows_to=d(rows_to) if rows_to else None,
    )


def report(*gaps: FetchGap) -> FetchReport:
    by_account: dict[str, list[FetchGap]] = {}
    for found in gaps:
        by_account.setdefault(found.account, []).append(found)
    return FetchReport(
        tuple(AccountOutlook(ref, tuple(found)) for ref, found in by_account.items()),
        TODAY,
    )


def statements_due(*accounts: str) -> AttentionItem:
    return AttentionItem(
        kind="statement-due",
        severity=HOUSEKEEPING,
        message="3 accounts have rows after their last known balance and none lately.",
        remedy="Upload the next statement for each.",
        href="/gaps",
        accounts=accounts,
    )


def titles(todos: tuple[Todo, ...]) -> list[str]:
    return [t.title for t in todos]


class TestAnOrdinaryDay:
    """Three files wanted for two accounts, one account with nothing to check against, and a
    statement known to be missing in the middle of an account's history."""

    def todos(self) -> tuple[Todo, ...]:
        return build_todos(
            overview(statements_due("everyday", "joint", "rainy")),
            report(
                gap(
                    "everyday",
                    GapKind.NEWER_STATEMENT,
                    "2026-08-01",
                    "2026-10-05",
                    closings=("2026-08-31", "2026-09-30"),
                    rows_to="2026-10-05",
                ),
                gap("everyday", GapKind.HOLE_BETWEEN, "2026-04-11", "2026-05-10"),
                gap("joint", GapKind.NEWER_STATEMENT, "2026-09-18", "2026-10-05"),
                gap("rainy", GapKind.NEWER_STATEMENT, "2026-09-15", "2026-10-05"),
                gap("holiday", GapKind.NO_BALANCE, "2026-06-01", "2026-09-30"),
            ),
            label_of,
        )

    def test_OrdinaryDay_StatementsDueNamingThreeAccounts_BecomesOneToDoPerAccountPerFile(
        self,
    ) -> None:
        todos = self.todos()
        uploads = [t for t in todos if t.kind.startswith("fetch-")]
        # everyday: two statements waiting (split at each expected closing) and one hole;
        # joint: one; rainy: one.
        assert [(t.account, t.title) for t in uploads] == [
            ("everyday", "Upload the statement covering 2026-08-01 to 2026-08-31"),
            ("everyday", "Upload the statement covering 2026-09-01 to 2026-09-30"),
            ("everyday", "Upload the statement covering 2026-04-11 to 2026-05-10"),
            ("joint", "Upload the statement covering 2026-09-18 to 2026-10-05"),
            ("rainy", "Upload the statement covering 2026-09-15 to 2026-10-05"),
        ]
        assert not [t for t in todos if t.kind == "statement-due"]

    def test_OrdinaryDay_EveryToDo_IsWhenConvenientAndNamesTheDayItDatesFrom(self) -> None:
        todos = self.todos()
        assert {t.urgency for t in todos} == {HOUSEKEEPING}
        assert all(t.since is not None for t in todos)
        assert [t.since for t in todos if t.account == "joint"] == [d("2026-09-18")]

    def test_OrdinaryDay_AnAccountWithNoKnownBalance_IsToldToConfirmOneForTheDay(self) -> None:
        (confirm,) = [t for t in self.todos() if t.kind == "confirm-balance"]
        assert confirm.title == "Confirm the balance for 2026-09-30"
        assert confirm.account == "holiday"
        assert confirm.control.label == "Confirm a balance"
        assert confirm.control.href == "/ledger?ref=holiday#opening"
        assert confirm.control.prescoped is True

    def test_BalanceToConfirm_WhenFirstTransactionIsYearsBeforeTheDayNamed_AgeIsFromTheDayNamed(
        self,
    ) -> None:
        """The account's first transaction is 2022-03-01, the day to confirm 2025-05-01: "Confirm
        the balance for 2025-05-01" has waited since that day, 17 months, and not four years."""
        (confirm,) = build_todos(
            overview(),
            report(gap("holiday", GapKind.NO_BALANCE, "2022-03-01", "2025-05-01")),
            label_of,
        )
        assert confirm.title == "Confirm the balance for 2025-05-01"
        assert confirm.since == d("2025-05-01")
        assert date_with_age(confirm.since, TODAY) == "2025-05-01 (over a year ago)"

    def test_OrdinaryDay_EveryToDo_HasAControlThatSaysWhatItDoes(self) -> None:
        todos = self.todos()
        assert todos
        for todo in todos:
            assert todo.control.href.startswith("/")
            assert todo.control.label not in ("", "Open")
        assert {t.control.label for t in todos if t.kind.startswith("fetch-")} == {"Upload"}

    def test_OrdinaryDay_UploadControls_OpenBringInScopedToTheAccountTheFileIsFor(self) -> None:
        uploads = [t for t in self.todos() if t.kind.startswith("fetch-")]
        assert uploads
        for todo in uploads:
            assert todo.account is not None
            assert todo.control.href == f"/bring-in?account={todo.account}"
            assert todo.control.prescoped is True

    def test_UploadControl_ForAnAccountWhoseReferenceNeedsEncoding_NamesTheSameAccount(
        self,
    ) -> None:
        (todo,) = build_todos(
            overview(),
            report(gap("joint & co #1", GapKind.NEWER_STATEMENT, "2026-09-01", "2026-09-30")),
            label_of,
        )
        assert todo.control.href == "/bring-in?account=joint%20%26%20co%20%231"

    def test_StatementsDue_WhereTheGapsCannotBeRead_AlsoOpenBringInForThatAccount(self) -> None:
        todos = build_todos(overview(statements_due("everyday", "joint")), None, label_of)
        assert [t.control.href for t in todos] == [
            "/bring-in?account=everyday",
            "/bring-in?account=joint",
        ]

    def test_OrdinaryDay_TheWhyLine_NamesNoAccountAndNoFigure(self) -> None:
        for todo in self.todos():
            for name in NAMES.values():
                assert name not in todo.why
            assert not any(ch in todo.why for ch in "£$")


class TestABadDay:
    def todos(self) -> tuple[Todo, ...]:
        return build_todos(
            overview(
                AttentionItem(
                    kind="consent",
                    severity=SOON,
                    message="Brook Bank's consent runs out on 2026-10-08.",
                    remedy="Reconnect the bank before consent lapses.",
                    href="/connections",
                    accounts=("joint",),
                ),
                AttentionItem(
                    kind="statement-fault",
                    severity=NOW,
                    message="Joint current: The statement closing on 2026-09-30 does not add up.",
                    remedy="Open the account's ledger.",
                    href="/ledger?ref=joint#opening",
                    accounts=("joint",),
                ),
            ),
            report(gap("rainy", GapKind.NEWER_STATEMENT, "2026-09-15", "2026-10-05")),
            label_of,
        )

    def test_BadDay_ToDos_ComeMostUrgentFirst(self) -> None:
        todos = self.todos()
        assert [t.urgency for t in todos] == [NOW, SOON, HOUSEKEEPING]
        assert titles(todos)[0] == "Find out why a statement does not add up"

    def test_BadDay_AFault_LeadsToTheAccountPageAndSaysWhereItGoes(self) -> None:
        fault = self.todos()[0]
        assert fault.control.label == "See the statement"
        assert fault.control.href == "/ledger?ref=joint#opening"
        assert fault.control.prescoped is True
        assert fault.account == "joint"

    def test_BadDay_TheWhyLine_DoesNotRepeatTheAccountNameTheToDoAlreadyCarries(self) -> None:
        assert self.todos()[0].why == "The statement closing on 2026-09-30 does not add up."

    def test_BadDay_AConsentRunningOut_IsToldToReconnect(self) -> None:
        consent = self.todos()[1]
        assert consent.control.label == "Reconnect"
        assert consent.control.href == "/connections"
        assert consent.urgency == SOON


class TestAClearDay:
    def test_ClearDay_NothingWaiting_NoToDos(self) -> None:
        assert build_todos(overview(), report(), label_of) == ()

    def test_ClearDay_AnAccountThatNeedsNothing_IsNotListed(self) -> None:
        quiet = FetchReport((AccountOutlook("everyday", ()),), TODAY)
        assert build_todos(overview(), quiet, label_of) == ()


class TestWhereTheFilesCouldNotBeWorkedOut:
    def test_StatementsDue_WithoutTheFilesWanted_StillBecomeOneToDoPerAccount(self) -> None:
        todos = build_todos(overview(statements_due("everyday", "joint")), None, label_of)
        assert [(t.account, t.control.label) for t in todos] == [
            ("everyday", "Upload"),
            ("joint", "Upload"),
        ]
        assert all(t.since is None for t in todos)


class TestOtherThingsATodoIsMadeOf:
    def test_FlaggedTransactions_AnInformationNote_IsStillSomethingToDecide(self) -> None:
        note = AttentionItem(
            kind="review",
            severity=INFORMATION,
            message="2 transactions are flagged for a decision that could not be made.",
            remedy="Answer each flag.",
            href="/review-flags",
            accounts=("joint", "rainy"),
        )
        (todo,) = build_todos(overview(note), report(), label_of)
        assert todo.control.label == "Decide"
        assert todo.control.href == "/review-flags"
        assert todo.account is None
        assert todo.accounts == ("joint", "rainy")
        assert todo.urgency == HOUSEKEEPING

    def test_ARebuildInProgress_AnInformationNote_IsNotSomethingToDo(self) -> None:
        note = AttentionItem(
            kind="rebuild-running",
            severity=INFORMATION,
            message="A rebuild is running.",
            remedy="Refresh later.",
            href="/diagnostics",
        )
        assert build_todos(overview(note), report(), label_of) == ()

    def test_AnExportThatStops_IsToldToImportAndNotToUploadAStatement(self) -> None:
        (todo,) = build_todos(
            overview(),
            report(gap("everyday", GapKind.EXPORT_STOPS, "2026-08-20", "2026-10-05")),
            label_of,
        )
        assert todo.title == "Import the export from 2026-08-20"
        assert todo.control.label == "Import"
        assert todo.control.href == "/bring-in?account=everyday"

    def test_AHoleKnownOnlyFromTheRhythmOfStatements_IsDrawnAsAGuess(self) -> None:
        (guess,) = build_todos(
            overview(),
            report(
                gap(
                    "everyday",
                    GapKind.HOLE_BETWEEN,
                    "2026-04-11",
                    "2026-05-10",
                    basis=Basis.INFERRED,
                )
            ),
            label_of,
        )
        assert guess.guess is True
        assert guess.why.startswith("Probably missing")

    def test_AHoleThatIsProven_IsNotAGuess(self) -> None:
        (proven,) = build_todos(
            overview(),
            report(gap("everyday", GapKind.HOLE_BETWEEN, "2026-04-11", "2026-05-10")),
            label_of,
        )
        assert proven.guess is False

    def test_AnAccountsRow_SaysWhatItWaitsFor(self) -> None:
        todos = build_todos(
            overview(),
            report(
                gap("joint", GapKind.NEWER_STATEMENT, "2026-09-18", "2026-10-05"),
                gap("holiday", GapKind.NO_BALANCE, "2026-06-01", "2026-09-30"),
            ),
            label_of,
        )
        assert {t.account: t.waiting for t in todos} == {
            "joint": "Statement wanted",
            "holiday": "Balance to confirm",
        }


class TestDaysThatAddUpAndAreNotLockedIn:
    def standing(self, *, locked: str | None, broken: bool = False) -> AccountStanding:
        known = (
            Known(d("2026-03-05"), "starling", "met", 1),
            Known(d("2026-03-20"), "x", "met", 2),
        )
        own = Agreement(
            AGREES,
            d("2026-03-05"),
            d("2026-03-20"),
            2,
            1,
            d("2026-03-20"),
            None,
            True,
            (),
            tested=(d("2026-03-20"),),
            tested_known=known,
            chain_tested=(d("2026-03-05"), d("2026-03-20")),
        )
        return AccountStanding(Standing(own, None), d(locked) if locked else None, broken)

    def test_Account_AddsUpAndNothingIsLocked_IsOfferedForLockingIn(self) -> None:
        assert lockable(self.standing(locked=None)) is True

    def test_Account_LockedIntoThePointItAddsUpTo_IsNotOfferedAgain(self) -> None:
        assert lockable(self.standing(locked="2026-03-20")) is False

    def test_Account_LockedPartWay_IsOfferedForTheRest(self) -> None:
        assert lockable(self.standing(locked="2026-03-05")) is True

    def test_Account_WithALockedStretchThatChanged_IsNotOfferedLockingInOnTop(self) -> None:
        assert lockable(self.standing(locked="2026-03-05", broken=True)) is False

    def test_Account_WithNoStanding_IsNotOffered(self) -> None:
        assert lockable(None) is False
