"""What an account's balance was, and so what it was before the history began.

An anchor is "the balance was X at the END of day D". The opening balance is
derived from the earliest anchor alone, and every later anchor is a check on
the rows between. Every expectation below was worked out from the scenario's
construction BEFORE the first run, and the working is in each docstring.

The account every scenario reads, `everyday`, GBP, one source, booked rows:

    r1  2026-03-02    -1,250  CAFE ONE
    r2  2026-03-05   +10,000  PAYMENT IN
    r3  2026-03-10    -2,000  RAIL
    r4  2026-03-15    -3,300  GARAGE        (the row planted as missing)
    r5  2026-03-20      +500  REFUND

Running sums of the rows, by the end of the day named:

    03-02  -1,250     03-05  +8,750     03-10  +6,750
    03-15  +3,450     03-20  +3,950     (nothing after 03-20)

so the rows' own total is +3,950, and the rows alone start from zero.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from datetime import date, timedelta

import pytest

from obdi.accounts import AccountRecord, AccountRef
from obdi.balance_anchors import (
    BANK,
    STATED,
    STATEMENT,
    Anchor,
    AnchorRefused,
    derive_opening,
    effective_opening,
    record_stated_anchor,
    remove_stated_anchor,
    stated_anchors,
)
from obdi.balance_reconciliation import balance_reconciliation
from obdi.ingest import import_file
from obdi.models import TransactionStatus
from obdi.rebuild import rebuild_from_raw
from obdi.store import Store
from obdi.synthetic_pdf import build_pdf
from test_balance_reconciliation import ACCOUNT as TRUELAYER_ACCOUNT
from test_balance_reconciliation import _built as build_bank_account
from test_ledger import land, txn

ACCOUNT = "everyday"
D = date

ROWS = [
    ("r1", D(2026, 3, 2), -1250, "CAFE ONE"),
    ("r2", D(2026, 3, 5), 10000, "PAYMENT IN"),
    ("r3", D(2026, 3, 10), -2000, "RAIL"),
    ("r4", D(2026, 3, 15), -3300, "GARAGE"),
    ("r5", D(2026, 3, 20), 500, "REFUND"),
]


def everyday(store: Store, *, omit: tuple[str, ...] = (), account: str = ACCOUNT) -> None:
    land(
        store,
        "digest-everyday",
        *(
            txn(account, "src-a", source_id, day, amount, description)
            for source_id, day, amount, description in ROWS
            if source_id not in omit
        ),
    )


def opening_of(store: Store, ref: str = ACCOUNT):
    return effective_opening(store, ref)


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "anchors.sqlite3") as opened:
        yield opened


class TestOneStatedAnchor:
    def test_Opening_WhenOneBalanceIsStated_IsThatBalanceLessTheRowsUpToIt(self, store):
        """Stated 1,000.00 at the end of 03-10. Rows through 03-10 sum to +6,750,
        so the account stood at 100,000 - 6,750 = 93,250 before its first row,
        which applies at the end of 03-01, the day before that row."""
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-03-10", "1000.00")

        opening = opening_of(store)

        assert opening.opening_minor == 93250
        assert opening.as_at == D(2026, 3, 1)
        assert opening.defining == Anchor(D(2026, 3, 10), 100000, STATED)

    def test_Opening_WhenOnlyOneAnchorExists_SaysItCannotBeTestedYet(self, store):
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-03-10", "1000.00")

        opening = opening_of(store)

        assert opening.single_anchor is True
        assert len(opening.readings) == 1
        assert opening.readings[0].agrees is None, "the defining anchor is not a check"

    def test_Opening_WhenNoAnchorExists_IsAbsentRatherThanZero(self, store):
        everyday(store)

        opening = opening_of(store)

        assert opening.opening_minor is None
        assert opening.as_at is None
        assert opening.readings == ()
        assert opening.defining is None

    def test_Opening_WhenTheStatedBalanceIsNegative_IsDerivedFromTheNegativeFigure(
        self, store
    ):
        """An overdrawn account: -50.00 at the end of 03-10 less +6,750 of rows is
        -5,000 - 6,750 = -11,750 before the first row."""
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-03-10", "-50.00")

        assert opening_of(store).opening_minor == -11750


class TestTheSumBoundaryIsOnOrBeforeTheAnchorDay:
    def test_Anchor_DatedBeforeTheFirstRow_IsTheOpeningItself(self, store):
        """No row is dated on or before 02-20, so the opening is the stated
        figure, and it applies at the end of 02-20 - earlier than the first row."""
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-02-20", "900.00")

        opening = opening_of(store)

        assert opening.opening_minor == 90000
        assert opening.as_at == D(2026, 2, 20)

    def test_Anchor_DatedOnTheDayOfTheFirstRow_CountsThatRow(self, store):
        """03-02 holds r1 (-1,250). Stated 987.50 at the end of that day means
        98,750 - (-1,250) = 100,000 before it. A sum that stopped short of the
        anchor's own day would give 98,750."""
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-03-02", "987.50")

        opening = opening_of(store)

        assert opening.opening_minor == 100000
        assert opening.as_at == D(2026, 3, 1)

    def test_Anchor_DatedOnALaterDayWithRows_CountsThatDaysRows(self, store):
        """03-05 holds r2 (+10,000): rows through it are +8,750, so stated 500.00
        gives 50,000 - 8,750 = 41,250. Excluding the day gives 51,250."""
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-03-05", "500.00")

        assert opening_of(store).opening_minor == 41250

    def test_Anchor_DatedAfterTheLastRow_CountsEveryRow(self, store):
        """All five rows sum to +3,950: stated 800.00 at the end of 04-30 gives
        80,000 - 3,950 = 76,050."""
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-04-30", "800.00")

        opening = opening_of(store)

        assert opening.opening_minor == 76050
        assert opening.as_at == D(2026, 3, 1)

    def test_Anchor_DatedTheDayBeforeTheFirstRow_AppliesOnThatSameDay(self, store):
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-03-01", "1000.00")

        opening = opening_of(store)

        assert opening.opening_minor == 100000
        assert opening.as_at == D(2026, 3, 1)

    def test_Anchor_OnAnAccountHoldingNoRows_IsTheOpeningAtItsOwnDate(self, store):
        store.declare_account(AccountRecord(ref=AccountRef("passbook"), label="Passbook"))
        record_stated_anchor(store, "passbook", "2026-05-01", "250.00")

        opening = opening_of(store, "passbook")

        assert opening.opening_minor == 25000
        assert opening.as_at == D(2026, 5, 1)


class TestLaterAnchorsAreChecksNotInputs:
    def test_SecondAnchor_WhenTheRowsCarryTheBalanceBetween_Agrees(self, store):
        """Opening 93,250 (stated 1,000.00 at 03-10). Expected at 03-20 is
        93,250 + 3,950 = 97,200, and 972.00 is stated, so it agrees by 0."""
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-03-10", "1000.00")
        record_stated_anchor(store, ACCOUNT, "2026-03-20", "972.00")

        opening = opening_of(store)

        check = opening.readings[1]
        assert check.defines_opening is False
        assert check.expected_minor == 97200
        assert check.difference_minor == 0
        assert check.agrees is True
        assert opening.differing == []
        assert opening.single_anchor is False

    def test_SecondAnchor_WhenARowBetweenIsMissing_DiffersByExactlyThatRowsAmount(
        self, tmp_path
    ):
        """r4 (-3,300, 03-15) is never landed. The opening is unaffected, being
        derived at 03-10, before the gap: 93,250. But the rows now reach
        93,250 + 7,250 = 100,500 at 03-20, while the true balance, 972.00, is
        97,200. The anchor is 3,300 BELOW the prediction: a difference of -3,300,
        the missing row's own amount and sign."""
        with Store(tmp_path / "gap.sqlite3") as gap:
            everyday(gap, omit=("r4",))
            record_stated_anchor(gap, ACCOUNT, "2026-03-10", "1000.00")
            record_stated_anchor(gap, ACCOUNT, "2026-03-20", "972.00")

            opening = opening_of(gap)

        check = opening.readings[1]
        assert opening.opening_minor == 93250
        assert check.expected_minor == 100500
        assert check.difference_minor == -3300
        assert check.agrees is False
        assert opening.differing == [check]

    def test_SecondAnchor_WhenAnExtraRowIsHeld_DiffersByThePositiveAmount(self, store):
        """A duplicate of r5 (+500) inflates the rows: predicted 97,700 against
        a stated 972.00 (97,200) is a difference of -500. A duplicated credit
        and a missing debit are different faults with different signs. It comes
        from the SAME source, because a second source's row of that amount is
        merged into r5 by the matcher (a first attempt used one and found the
        rows unchanged); the same-source rule is what keeps a twin apart."""
        everyday(store)
        land(store, "digest-dup", txn(ACCOUNT, "src-a", "dup", D(2026, 3, 18), 500, "REFUND TWIN"))
        record_stated_anchor(store, ACCOUNT, "2026-03-10", "1000.00")
        record_stated_anchor(store, ACCOUNT, "2026-03-20", "972.00")

        assert opening_of(store).readings[1].difference_minor == -500

    def test_FaultBeforeTheEarliestAnchor_IsAbsorbedIntoTheOpeningAndNothingDiffers(
        self, tmp_path
    ):
        """The documented weakness. r1 (-1,250, before both anchors) is
        missing. With stated 1,000.00 at 03-10 the rows through then sum to
        +8,000 (not +6,750), so the opening becomes 92,000 - wrong by the
        missing row, and the second anchor (972.00 at 03-20) still agrees
        because the later rows are complete."""
        with Store(tmp_path / "absorbed.sqlite3") as absorbed:
            everyday(absorbed, omit=("r1",))
            record_stated_anchor(absorbed, ACCOUNT, "2026-03-10", "1000.00")
            record_stated_anchor(absorbed, ACCOUNT, "2026-03-20", "972.00")

            opening = opening_of(absorbed)

        assert opening.opening_minor == 92000
        assert opening.readings[1].agrees is True

    def test_Anchors_GivenInAnyOrder_TheEarliestStillDefinesTheOpening(self):
        rows = [
            txn(ACCOUNT, "s", source_id, day, amount, description)
            for source_id, day, amount, description in ROWS
        ]
        early = Anchor(D(2026, 3, 10), 100000, STATED)
        late = Anchor(D(2026, 3, 20), 97200, STATED)

        forwards = derive_opening(ACCOUNT, [early, late], rows)
        backwards = derive_opening(ACCOUNT, [late, early], rows)

        assert forwards == backwards
        assert forwards.defining == early

    def test_SameDayAnchors_TheStatedOneDefinesAndTheBankOneIsChecked(self):
        """A person's word outranks a feed's on one day. The bank's figure is
        then the check, and differs by exactly the gap between the two."""
        rows = [
            txn(ACCOUNT, "s", source_id, day, amount, description)
            for source_id, day, amount, description in ROWS
        ]
        stated = Anchor(D(2026, 3, 10), 100000, STATED)
        bank = Anchor(D(2026, 3, 10), 99000, BANK)

        opening = derive_opening(ACCOUNT, [bank, stated], rows)

        assert opening.defining == stated
        assert opening.readings[1].difference_minor == -1000


class TestWhichRowsCount:
    def _rows(self):
        return [
            txn(ACCOUNT, "s", "b", D(2026, 3, 2), -1000, "BOOKED ONE"),
            txn(
                ACCOUNT, "s", "p", D(2026, 3, 3), -400, "PENDING ONE",
                status=TransactionStatus.PENDING,
            ),
            txn(
                ACCOUNT, "s", "v", D(2026, 3, 4), -9999, "VOID ONE",
                status=TransactionStatus.VOID,
            ),
        ]

    @pytest.mark.parametrize(
        ("basis", "expected"),
        [(STATED, 51400), (BANK, 51000), (STATEMENT, 51000)],
    )
    def test_Void_NeverCountsForAnyBasis(self, basis, expected):
        """Booked -1,000 always counts and the void -9,999 never does; the
        pending -400 counts only for a stated balance (see below). Were the void
        row counted every figure would be 9,999 higher."""
        anchor = Anchor(D(2026, 3, 31), 50000, basis)

        assert derive_opening(ACCOUNT, [anchor], self._rows()).opening_minor == expected

    def test_Pending_CountsAgainstAStatedBalanceButNotAgainstABookedOne(self):
        """A person states the account as they see it, pending included, so
        50,000 - (-1,000 - 400) = 51,400. The bank's and a statement's figures
        are booked balances, which never held the pending row: 50,000 + 1,000."""
        stated = derive_opening(ACCOUNT, [Anchor(D(2026, 3, 31), 50000, STATED)], self._rows())
        bank = derive_opening(ACCOUNT, [Anchor(D(2026, 3, 31), 50000, BANK)], self._rows())
        statement = derive_opening(
            ACCOUNT, [Anchor(D(2026, 3, 31), 50000, STATEMENT)], self._rows()
        )

        assert stated.opening_minor == 51400
        assert bank.opening_minor == 51000
        assert statement.opening_minor == 51000

    def test_ForeignCurrencyRows_WithholdTheOpeningInsteadOfSummingUnlikeUnits(self):
        rows = [replace(row, currency="EUR") for row in self._rows()[:1]]

        opening = derive_opening(ACCOUNT, [Anchor(D(2026, 3, 31), 50000, STATED)], rows)

        assert opening.opening_minor is None
        assert "GBP" in opening.withheld
        assert len(opening.readings) == 1, "the anchor is still listed"


class TestStatedAnchorsAreRefusedBeforeAnythingIsWritten:
    @pytest.mark.parametrize(
        ("day", "amount", "currency", "why"),
        [
            ("2999-01-01", "10.00", "GBP", "a date that has not happened"),
            ("2026-13-01", "10.00", "GBP", "not a calendar date"),
            ("01/03/2026", "10.00", "GBP", "not the written form"),
            ("2026-03-10", "12.345", "GBP", "sub-penny precision"),
            ("2026-03-10", "twelve", "GBP", "not a figure"),
            ("2026-03-10", "1e5", "GBP", "exponent form"),
            ("2026-03-10", "Infinity", "GBP", "not finite"),
            ("2026-03-10", "NaN", "GBP", "not a number"),
            ("2026-03-10", "€10.00", "GBP", "another currency's symbol"),
            ("2026-03-10", "", "GBP", "empty"),
            ("2026-03-10", "10.00", "EUR", "another currency"),
            ("2026-03-10", "10.00", "", "no currency"),
        ],
    )
    def test_Refused_LeavesNothingStated(self, store, day, amount, currency, why):
        everyday(store)

        with pytest.raises(AnchorRefused):
            record_stated_anchor(store, ACCOUNT, day, amount, currency=currency)

        assert stated_anchors(store, ACCOUNT) == [], why

    def test_UnknownAccount_IsRefusedAndNothingIsStatedUnderTheName(self, store):
        everyday(store)

        with pytest.raises(AnchorRefused, match="no account"):
            record_stated_anchor(store, "no-such-account", "2026-03-10", "10.00")

        assert stated_anchors(store, "no-such-account") == []

    def test_Refusal_NeverQuotesTheAmountThatWasTyped(self, store):
        everyday(store)
        for typed in ("7777.777", "7777,77x", "7777.00 EUR"):
            with pytest.raises(AnchorRefused) as refused:
                record_stated_anchor(store, ACCOUNT, "2026-03-10", typed)
            assert "7777" not in str(refused.value), typed

    def test_Today_IsAcceptedAndTomorrow_IsNot(self, store):
        everyday(store)
        today = date(2026, 6, 15)

        record_stated_anchor(store, ACCOUNT, "2026-06-15", "10.00", today=today)
        with pytest.raises(AnchorRefused):
            record_stated_anchor(
                store, ACCOUNT, (today + timedelta(days=1)).isoformat(), "10.00", today=today
            )

        assert [a.day for a in stated_anchors(store, ACCOUNT)] == [today]

    def test_DeclaredAccountWithNoRows_IsKnownEnoughToStateABalanceFor(self, store):
        store.declare_account(AccountRecord(ref=AccountRef("tin"), label="Cash tin"))

        anchor = record_stated_anchor(store, "tin", "2026-03-10", "-3.50")

        assert anchor.balance_minor == -350


class TestStatingAndRemoving:
    def test_StatingTheSameDateTwice_ReplacesTheEarlierFigure(self, store):
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-03-10", "1000.00")
        record_stated_anchor(store, ACCOUNT, "2026-03-10", "1100.00")

        assert stated_anchors(store, ACCOUNT) == [Anchor(D(2026, 3, 10), 110000, STATED)]
        assert opening_of(store).opening_minor == 110000 - 6750

    def test_Removing_TheOnlyAnchor_LeavesNoOpeningBalance(self, store):
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-03-10", "1000.00")

        assert remove_stated_anchor(store, ACCOUNT, "2026-03-10") is True

        assert opening_of(store).opening_minor is None

    def test_Removing_AnAnchorThatIsNotThere_SaysSoAndChangesNothing(self, store):
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-03-10", "1000.00")

        assert remove_stated_anchor(store, ACCOUNT, "2026-03-11") is False

        assert len(stated_anchors(store, ACCOUNT)) == 1

    def test_Removing_TheEarliestOfTwo_PromotesTheLaterToDefineTheOpening(self, store):
        everyday(store)
        record_stated_anchor(store, ACCOUNT, "2026-03-10", "1000.00")
        record_stated_anchor(store, ACCOUNT, "2026-03-20", "972.00")

        remove_stated_anchor(store, ACCOUNT, "2026-03-10")
        opening = opening_of(store)

        # 97,200 less rows through 03-20 (+3,950).
        assert opening.opening_minor == 93250
        assert opening.single_anchor is True

    def test_Anchors_ForOneAccount_NeverAppearOnAnother(self, store):
        everyday(store)
        everyday(store, account="other")
        record_stated_anchor(store, ACCOUNT, "2026-03-10", "1000.00")

        assert opening_of(store, "other").opening_minor is None

    def test_StatedAnchors_AreCountedAsIrreplaceableWork(self, store):
        everyday(store)
        assert store.irreplaceable()["known balances stated by you"] == 0

        record_stated_anchor(store, ACCOUNT, "2026-03-10", "1000.00")

        assert store.irreplaceable()["known balances stated by you"] == 1


class TestStatedAnchorsOutliveTheirAccountsDerivedLayer:
    def test_RebuildFromRaw_KeepsEveryStatedAnchorAndTheOpeningStillDerives(self, tmp_path):
        """A rebuild wipes and replays the transaction layer; what a person
        stated has no artefact to replay it from. The bank's own anchors are
        earlier, so they define the opening (100,000) before and after, and the
        stated 5,000.00 at 03-06 must still be there as a check."""
        with Store(tmp_path / "rebuilt.sqlite3") as held:
            build_bank_account(held)
            record_stated_anchor(held, TRUELAYER_ACCOUNT, "2026-03-06", "5000.00")
            before = opening_of(held, TRUELAYER_ACCOUNT)

            rebuild_from_raw(held)
            after = opening_of(held, TRUELAYER_ACCOUNT)

            assert stated_anchors(held, TRUELAYER_ACCOUNT) == [
                Anchor(D(2026, 3, 6), 500000, STATED)
            ]
        assert before.opening_minor == after.opening_minor
        assert before.defining == after.defining

    def test_CanonicalRename_CarriesAStatedAnchorToTheNewName(self, store):
        stored = store.declare_account(
            AccountRecord(ref=AccountRef("halifax-current"), label="Halifax")
        )
        record_stated_anchor(store, "halifax-current", "2026-03-10", "1000.00")

        store.declare_account(replace(stored, ref=AccountRef("halifax-everyday")))

        assert stated_anchors(store, "halifax-everyday") == [
            Anchor(D(2026, 3, 10), 100000, STATED)
        ]
        assert stated_anchors(store, "halifax-current") == []

    def test_DisplayRename_LeavesTheAnchorWhereItWas(self, store):
        stored = store.declare_account(
            AccountRecord(ref=AccountRef("halifax-current"), label="Halifax")
        )
        record_stated_anchor(store, "halifax-current", "2026-03-10", "1000.00")

        store.declare_account(replace(stored, label="Everyday"))

        assert len(stated_anchors(store, "halifax-current")) == 1

    def test_Rebind_CarriesAStatedAnchorWithTheRowsItWasStatedAbout(self, store):
        everyday(store, account="truelayer:abc")
        record_stated_anchor(store, "truelayer:abc", "2026-03-10", "1000.00")

        store.rebind_account("truelayer:abc", "halifax-current")

        moved = opening_of(store, "halifax-current")
        assert moved.opening_minor == 93250
        assert stated_anchors(store, "truelayer:abc") == []

    def test_Rebind_WhenTheNewNameAlreadyStatesThatDate_KeepsItsFigureAndLosesNothing(
        self, store
    ):
        """Two accounts each state 03-10. The rename must not choose between
        them: the destination's figure stands and the other stays under its old
        name for a person to resolve."""
        everyday(store, account="truelayer:abc")
        land(store, "digest-h", txn("halifax-current", "src-a", "h1", D(2026, 3, 1), -100, "OTHER"))
        record_stated_anchor(store, "truelayer:abc", "2026-03-10", "1000.00")
        record_stated_anchor(store, "halifax-current", "2026-03-10", "2000.00")

        store.rebind_account("truelayer:abc", "halifax-current")

        assert stated_anchors(store, "halifax-current")[0].balance_minor == 200000
        assert stated_anchors(store, "truelayer:abc")[0].balance_minor == 100000


class TestTheBanksOwnRunningBalanceIsAnAnchor:
    def test_BankAnchors_ComeFromTheReconciliationsOwnOpeningAndEveryDaysClosing(self, store):
        """The reconciliation's opening is 100,000 on 03-02 and its days close at
        98,266 (03-02), 123,266 (03-03), 120,489 (03-05) and 119,889 (03-06) (see
        test_balance_reconciliation). As anchors they are the end of 03-01 and the end
        of each of those days, and the rows reproduce every one."""
        build_bank_account(store)
        reconciliation = next(
            a for a in balance_reconciliation(store).accounts
            if a.account_id == TRUELAYER_ACCOUNT
        )
        assert reconciliation.opening is not None

        opening = opening_of(store, TRUELAYER_ACCOUNT)

        found = [(r.anchor.day, r.anchor.balance_minor, r.anchor.basis) for r in opening.readings]
        assert found == [
            (D(2026, 3, 1), reconciliation.opening.opening_minor, BANK),
            (D(2026, 3, 2), 98266, BANK),
            (D(2026, 3, 3), 123266, BANK),
            (D(2026, 3, 5), 120489, BANK),
            (D(2026, 3, 6), 119889, BANK),
        ]
        assert opening.opening_minor == 100000
        assert opening.as_at == D(2026, 3, 1)
        assert [r.agrees for r in opening.readings[1:]] == [True, True, True, True]

    def test_BankClosing_WhenARowIsMissingFromTheStore_DiffersFromItsDayByThatRow(self, tmp_path):
        """Row d (-2,000, 03-05) is omitted from what is landed while the bank's
        balances still reflect it. 03-05's single remaining row still chains, closing
        at 120,489 which includes d; held rows predict 122,489 there, so the bank
        differs by -2,000 from 03-05 on, and the days before it agree."""
        with Store(tmp_path / "omitted.sqlite3") as held:
            build_bank_account(held, omit=("d",))

            opening = opening_of(held, TRUELAYER_ACCOUNT)

        assert opening.opening_minor == 100000
        assert [r.difference_minor for r in opening.readings[1:]] == [0, 0, -2000, -2000]
        assert opening.readings[3].anchor.day == D(2026, 3, 5)
        assert opening.readings[3].expected_minor == 122489

    def test_AccountWithNoRunningBalance_HasNoBankAnchor(self, store):
        everyday(store)

        opening = opening_of(store)

        assert opening.readings == ()
        assert BANK not in {r.anchor.basis for r in opening.readings}

    def test_ABankAccountsAnchorsAreNotStored(self, store):
        build_bank_account(store)
        opening_of(store, TRUELAYER_ACCOUNT)

        assert store.valuations_for(f"account:{TRUELAYER_ACCOUNT}") == []

    def test_AStatedAnchorOnABankAccount_JoinsTheBanksAsACheck(self, store):
        """Stated 5,000.00 at 03-06 against a bank-derived opening of 100,000:
        the rows predict 100,000 + 19,889 = 119,889 at 03-06, so a stated
        5,000.00 (500,000) differs by 380,111 - shown, not absorbed. The bank's
        own anchors define the opening because they are earlier."""
        build_bank_account(store)
        record_stated_anchor(store, TRUELAYER_ACCOUNT, "2026-03-06", "5000.00")

        opening = opening_of(store, TRUELAYER_ACCOUNT)

        assert opening.opening_minor == 100000
        stated = next(r for r in opening.readings if r.anchor.basis == STATED)
        assert stated.difference_minor == 500000 - 119889


# A statement the Santander reader claims. Closing 1,010.00 owed after a 10.00
# purchase on a balance of 1,000.00 owed, dated 11 July 2026. In the store's
# convention - spending negative, so money OWED is a negative position - the
# statement's opening is -100,000, the purchase -1,000, and its closing -101,000.
def _santander(*, closing: str = "1,010.00", brought: str = "1,000.00") -> bytes:
    return build_pdf(
        [
            "Santander UK plc. Registered Office: 2 Triton Square",
            "Statement Date: 11th July 2026      Page No: 4 / 4",
            "Account credit limit:            3,000.00",
            f"Balance brought forward from previous statement          {brought}",
            "29th Jun    Some Shop Somewhere GB                          10.00",
            f"Balance {closing} Interest  0.000% to 11-03-2027",
            f"Your new balance:                                        {closing}",
        ]
    )


def _file(store: Store, tmp_path, name: str, payload: bytes, account: str = "card") -> None:
    path = tmp_path / name
    path.write_bytes(payload)
    import_file(store, path, account_id=account)


class TestAStatementsClosingBalanceIsAnAnchor:
    def test_ClosingBalance_OfACardStatement_IsNegativeBecauseMoneyOwedIsANegativePosition(
        self, store, tmp_path
    ):
        """The statement's own printed opening was 1,000.00 owed. In this
        store's convention that is -100,000, and the walk through its rows
        reaches a closing of -101,000. The derived opening must therefore
        reproduce the printed opening, -100,000: a wrong sign on the closing
        would give +101,000 - (-1,000) = +102,000 instead."""
        _file(store, tmp_path, "july.pdf", _santander())

        opening = opening_of(store, "card")

        statement = opening.readings[0].anchor
        assert statement.basis == STATEMENT
        assert statement.day == D(2026, 7, 11)
        assert statement.balance_minor == -101000
        assert opening.opening_minor == -100000
        assert opening.as_at == D(2026, 6, 28)

    def test_TwoStatements_TheLaterOneChecksTheRowsBetween(self, store, tmp_path):
        """A June statement closing at 1,000.00 owed and the July one above. The
        July statement's own rows (-1,000) carry -100,000 to -101,000, so the
        later anchor agrees with the earlier one only if the June rows reach the
        store. Here June has no rows of its own, so it defines the opening and
        July is checked against it."""
        june = build_pdf(
            [
                "Santander UK plc. Registered Office: 2 Triton Square",
                "Statement Date: 11th June 2026      Page No: 4 / 4",
                "Account credit limit:            3,000.00",
                "Balance brought forward from previous statement          1,000.00",
                "Balance 1,000.00 Interest  0.000% to 11-03-2027",
                "Your new balance:                                        1,000.00",
            ]
        )
        _file(store, tmp_path, "june.pdf", june)
        _file(store, tmp_path, "july.pdf", _santander())

        opening = opening_of(store, "card")

        assert [r.anchor.day for r in opening.readings] == [D(2026, 6, 11), D(2026, 7, 11)]
        assert opening.opening_minor == -100000
        assert opening.readings[1].agrees is True

    def test_Statement_WhoseRowsDoNotCarryItsOpeningToItsClosing_IsNotUsedAndIsCounted(
        self, store, tmp_path
    ):
        """Closing 1,011.00 against rows that reach 1,010.00: the reading does
        not reconcile, so its closing balance could be misread in sign or
        figure. It is left out - an opening with the wrong sign would be a
        confident wrong figure - and counted, so the quiet is not a pass."""
        payload = _santander(closing="1,011.00")
        path = tmp_path / "bad.pdf"
        path.write_bytes(payload)
        # The door refuses the rows; the artefact stays held, which is the point.
        with contextlib.suppress(Exception):
            import_file(store, path, account_id="card")

        opening = opening_of(store, "card")

        assert opening.readings == ()
        assert opening.opening_minor is None
        assert opening.unusable_statements == 1

    def test_AccountWithNoStatements_ReportsNoneUnusable(self, store):
        everyday(store)

        assert opening_of(store).unusable_statements == 0

    def test_Statement_FiledUnderAnotherAccount_IsNotThisAccountsAnchor(
        self, store, tmp_path
    ):
        _file(store, tmp_path, "july.pdf", _santander(), account="card-two")

        assert opening_of(store, "card").readings == ()
        assert len(opening_of(store, "card-two").readings) == 1
