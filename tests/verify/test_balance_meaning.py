"""What a source's stated balance means, decided from the source's own arithmetic.

The household is the one in test_family_anchors (September 2026, one main
account, the Bills Space, 800.00 in main before the 1st). Two exports of it
are invented, each with a balance column worked by hand:

  whole-account export   `EXPORT_ROWS` there: the balance moves with every
                         listed row, a Space payment (the 12th water bill, the
                         20th gym) included, and never with a transfer.
  main-only export       `MAIN_ONLY_ROWS` here: the balance does NOT move for a
                         Space payment and DOES move for a transfer to a Space,
                         which the export does not list.

Main's balance at the end of each day, by hand, from 800.00 held in main:

    day  what                          main
     1   salary                        380000
     2   transfer to Bills            -40000 -> 340000
     3   (end of the 3rd)              340000   <- the export's opening
     4   cafe                          -2500  -> 337500
    12   water bill, paid from Bills   unmoved 337500
    15   grocer                        -7000  -> 330500
    16   transfer to Bills (unlisted)  -2000  -> 328500
    20   gym, paid from Bills          unmoved 328500
    22   garage                        -9000  -> 319500
    25   refund                       +120000 -> 439500

The steps that tell the readings apart are 4 to 12 (a Space payment) and 15 to
20 (a Space payment and a transfer). The main-only export follows both under
the main-only reading and neither under the whole-account one.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta

import pytest

from obdi.ingest.family_anchors import families_of
from obdi.ingest.identity import content_key
from obdi.ingest.store import Store
from obdi.verify.balance_anchors import (
    EXPORT,
    FAMILY,
    effective_opening,
)
from obdi.verify.balance_meaning import (
    BOTH,
    NEITHER,
    READING_THRESHOLD,
    UNDECIDED,
    WHOLE,
)
from obdi.verify.balance_meaning import MAIN as READ_AS_MAIN
from test_family_anchors import (
    EXPORT_ROWS,
    STATEMENT,
    TRUE_MAIN_OPENING,
    drop_row,
    feed_rows,
    import_export,
    import_statement,
)
from test_space_attribution import BILLS, FEED, MAIN, MAP, Household, pay

CSV = "starling-csv"
STATEMENT_SOURCE = "starling-statement-pdf"

MAIN_ONLY_ROWS = [
    "04/09/2026,Cafe,Cafe,FASTER PAYMENT,-25.00,3375.00,",
    "12/09/2026,Water Co,Water Co,FASTER PAYMENT,-50.00,3375.00,",
    "15/09/2026,Grocer,Grocer,FASTER PAYMENT,-70.00,3305.00,",
    "20/09/2026,Gym,Gym,FASTER PAYMENT,-30.00,3285.00,",
    "22/09/2026,Garage,Garage,FASTER PAYMENT,-90.00,3195.00,",
    "25/09/2026,Refund,Refund,FASTER PAYMENT,1200.00,4395.00,",
]

#: Balances no reading of this household produces.
BROKEN_ROWS = [
    "04/09/2026,Cafe,Cafe,FASTER PAYMENT,-25.00,3375.00,",
    "12/09/2026,Water Co,Water Co,FASTER PAYMENT,-50.00,111.11,",
    "15/09/2026,Grocer,Grocer,FASTER PAYMENT,-70.00,222.22,",
    "20/09/2026,Gym,Gym,FASTER PAYMENT,-30.00,333.33,",
    "22/09/2026,Garage,Garage,FASTER PAYMENT,-90.00,444.44,",
]


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "meaning.sqlite3") as opened:
        yield opened


@pytest.fixture
def home(store):
    household = Household(store, MAP)
    household.arrive(*feed_rows())
    return household


def opening_of(home: Household):
    return effective_opening(home.store, MAIN_REF, families=families_of(home.store, MAP))


MAIN_REF = MAIN


def meaning_of(home: Household, source: str):
    found = {m.source: m for m in opening_of(home).meanings}
    assert source in found, sorted(found)
    return found[source]


class TestAnExportWhoseBalanceIsTheMainAccountsOwn:
    def test_Export_WhenItsBalanceIgnoresSpacePaymentsAndMovesOnTransfers_IsReadAsMainOnly(
        self, home, tmp_path
    ):
        import_export(home.store, tmp_path, MAIN_ONLY_ROWS)

        meaning = meaning_of(home, CSV)

        assert (meaning.verdict, meaning.steps, meaning.main, meaning.whole) == (
            READ_AS_MAIN, 2, 2, 0,
        )

    def test_Anchors_WhenTheExportIsMainOnly_AreTheMainAccountsOwnAndAllAgree(
        self, home, tmp_path
    ):
        import_export(home.store, tmp_path, MAIN_ONLY_ROWS)

        opening = opening_of(home)

        assert {r.anchor.basis for r in opening.readings} == {EXPORT}
        assert [(r.anchor.day.day, r.anchor.balance_minor) for r in opening.readings] == [
            (3, 340000), (4, 337500), (12, 337500), (15, 330500),
            (20, 328500), (22, 319500), (25, 439500),
        ]
        assert [r.agrees for r in opening.readings] == [None, True, True, True, True, True, True]
        assert opening.opening_minor == TRUE_MAIN_OPENING
        assert opening.family is None

    def test_Walk_WhenARowIsMissing_DatesTheFaultAgainstTheMainAccountsOwnRows(
        self, home, tmp_path
    ):
        import_export(home.store, tmp_path, MAIN_ONLY_ROWS)
        drop_row(home.store, MAIN, -7000, 15)

        opening = opening_of(home)

        assert opening.differing
        assert opening.differing[0].anchor.day == date(2026, 9, 15)
        assert opening.differing[0].difference_minor == -7000
        assert opening.readings[opening.readings.index(opening.differing[0]) - 1].anchor.day == (
            date(2026, 9, 12)
        )

    def test_Export_WhenReadAsWholeAccount_WouldHaveDifferedAlmostEverywhere(self, home, tmp_path):
        # The measured old behaviour: the same export compared with the family's rows.
        import_export(home.store, tmp_path, MAIN_ONLY_ROWS)
        from obdi.ingest.family_anchors import FamilyAnchor, FamilyAnchors
        from obdi.verify.balance_anchors import walk_family

        members = {
            ref: home.store.transactions_for_account(ref)
            for ref in (MAIN, *families_of(home.store, MAP).spaces_of(MAIN))
        }
        anchors = FamilyAnchors(
            tuple(
                FamilyAnchor(r.anchor.day, r.anchor.balance_minor, CSV)
                for r in opening_of(home).readings
            )
        )

        walked = walk_family(MAIN, anchors, members)

        assert len(walked.differing) >= 3
        assert walked.constant is False


class TestAnExportWhoseBalanceIsTheWholeAccounts:
    def test_Export_WhenItsBalanceMovesWithEverySpacePayment_IsReadAsWhole(self, home, tmp_path):
        import_export(home.store, tmp_path, EXPORT_ROWS)

        meaning = meaning_of(home, CSV)

        assert (meaning.verdict, meaning.steps, meaning.whole, meaning.main) == (WHOLE, 2, 2, 0)

    def test_Walk_WhenTheExportIsWholeAccountAndRowsAreRight_AgreesOnEveryDay(
        self, home, tmp_path
    ):
        import_export(home.store, tmp_path, EXPORT_ROWS)

        opening = opening_of(home)

        assert opening.family is not None
        assert (opening.family.anchors, len(opening.family.differing)) == (6, 0)
        assert {r.anchor.basis for r in opening.readings} == {FAMILY}
        assert opening.opening_minor == TRUE_MAIN_OPENING

    def test_Walk_WhenTheExportIsWholeAccountAndARowIsMissing_DatesTheFault(self, home, tmp_path):
        import_export(home.store, tmp_path, EXPORT_ROWS)
        drop_row(home.store, MAIN, -9000, 22)

        walked = opening_of(home).family

        assert walked is not None
        assert walked.first_differing is not None
        assert walked.first_differing.day == date(2026, 9, 22)


class TestASourceThatCannotBeDecided:
    def test_Export_WhenItsBalancesFitNeitherReading_IsUsedForNothingAndSaysSo(
        self, home, tmp_path
    ):
        import_export(home.store, tmp_path, BROKEN_ROWS)

        opening = opening_of(home)
        meaning = meaning_of(home, CSV)

        assert (meaning.verdict, meaning.whole, meaning.main) == (NEITHER, 0, 0)
        assert opening.readings == ()
        assert opening.family is None
        assert opening.opening_minor is None

    def test_Export_WhenNoSpacePaymentOrTransferFallsBetweenItsBalances_IsUsedForNothing(
        self, store, tmp_path
    ):
        # A household with no Space activity at all: both readings predict
        # every step identically, so neither can be preferred.
        Household(store, MAP).arrive(pay(MAIN, FEED, "f-salary", 300000, 1, "Employer"))
        import_export(
            store,
            tmp_path,
            [
                "02/09/2026,Cafe,Cafe,FASTER PAYMENT,-25.00,3975.00,",
                "05/09/2026,Cafe,Cafe,FASTER PAYMENT,-10.00,3965.00,",
            ],
        )

        meaning = meaning_of(Household(store, MAP), CSV)

        assert (meaning.verdict, meaning.steps) == (UNDECIDED, 0)
        assert effective_opening(store, MAIN, families=families_of(store, MAP)).readings == ()


class TestTheStatementAndTheExportDisagreeingInMeaning:
    """The real case: on one account the certified statement states the whole
    account's balance and the export states the main account's own."""

    def test_EachSource_IsUsedUnderItsOwnReading(self, home, tmp_path):
        import_statement(home.store, tmp_path, STATEMENT)
        import_export(home.store, tmp_path, MAIN_ONLY_ROWS)

        found = {m.source: m.verdict for m in opening_of(home).meanings}

        assert found == {STATEMENT_SOURCE: WHOLE, CSV: READ_AS_MAIN}

    def test_BothWalks_WhenTheRowsAreRight_AgreeOnEveryDay(self, home, tmp_path):
        import_statement(home.store, tmp_path, STATEMENT)
        import_export(home.store, tmp_path, MAIN_ONLY_ROWS)

        opening = opening_of(home)

        assert opening.family is not None
        assert opening.family.sources == (STATEMENT_SOURCE,)
        assert (opening.family.anchors, len(opening.family.differing)) == (7, 0)
        assert opening.differing == []
        assert {r.anchor.basis for r in opening.readings} == {EXPORT, FAMILY}
        assert opening.opening_minor == TRUE_MAIN_OPENING

    def test_BothWalks_WhenARowIsMissing_EachDatesTheSameFault(self, home, tmp_path):
        import_statement(home.store, tmp_path, STATEMENT)
        import_export(home.store, tmp_path, MAIN_ONLY_ROWS)
        drop_row(home.store, MAIN, -9000, 22)

        opening = opening_of(home)

        assert opening.family is not None
        assert opening.family.first_differing is not None
        assert opening.family.first_differing.day == date(2026, 9, 22)
        assert opening.differing
        assert opening.differing[0].anchor.day == date(2026, 9, 22)


class TestStatementMembershipAppliesToStatementAnchorsOnly:
    """`derive_opening(placed=)` moves a row onto the day a statement lists it for
    a STATEMENT anchor. A main-only export's anchors (basis `export`) are the
    main account's own and go by stored date, placement untouched."""

    def test_Placement_MovesARowForAStatementAnchorButNotForAnExportOne(self):
        from obdi.verify.balance_anchors import STATEMENT, Anchor, derive_opening

        row = replace(
            pay(MAIN, FEED, "f-row", -7000, 15, "Grocer"),
            entity_id="e-row",
        )
        later = date(2026, 9, 20)
        placed = {"e-row": later}
        day = date(2026, 9, 15)

        statement = derive_opening(MAIN, [Anchor(day, 100000, STATEMENT)], [row], placed=placed)
        export = derive_opening(MAIN, [Anchor(day, 100000, EXPORT)], [row], placed=placed)

        # The statement lists the row after the 15th, so its balance at the 15th
        # does not include it; the export goes by the row's own date.
        assert statement.opening_minor == 100000
        assert export.opening_minor == 100000 + 7000


class TestARowFoldedAsTheSameMoneyIsNotASpacePayment:
    """The statement-period pass (`same_money_fold`) folds a feed row that is
    the same money as a statement's rows. It leaves no copy on any Space row,
    which is how the store tells it from a Space fold; the grocer here stands in
    for such a row, folded the way that pass folds it."""

    @staticmethod
    def fold_the_grocer(home: Household) -> None:
        grocer = next(
            t
            for t in home.store.transactions_for_account(MAIN)
            if t.amount_minor == -7000 and t.value_date == date(2026, 9, 15)
        )
        home.store.replace_statement_folds([grocer.entity_id])
        assert grocer.entity_id in home.store.statement_folded_ids()
        assert grocer.entity_id not in home.store.space_folded_ids()

    def test_Reading_KeepsItAsAMainRowAndCountsOnlySpaceFoldsAsSpacePayments(
        self, home, tmp_path
    ):
        import_export(home.store, tmp_path, MAIN_ONLY_ROWS)
        self.fold_the_grocer(home)

        from obdi.verify.balance_meaning import _held

        every, unfolded, folded = _held(home.store, MAIN, [CSV]).listed[CSV]

        assert sorted(folded.minors) == [-5000, -3000], "the water bill and the gym only"
        assert -7000 in unfolded.minors
        assert -7000 in every.minors
        assert meaning_of(home, CSV).verdict == READ_AS_MAIN

    def test_FamilySum_ExcludesItExactlyAsItExcludesASpaceFoldedRow(self, home, tmp_path):
        import_export(home.store, tmp_path, EXPORT_ROWS)
        self.fold_the_grocer(home)

        walked = opening_of(home).family

        assert walked is not None
        assert walked.first_differing is not None
        assert walked.first_differing.day == date(2026, 9, 15)
        assert walked.first_differing.difference_minor == -7000


def cycle_rows(kind: str, cycles: int, corrupt: frozenset[int] = frozenset()):
    """A longer invented household: every three days a main payment, then a
    Space payment, then a transfer to the Space. Returns the feed's rows and an
    export's lines whose balance is of the given kind ("main" or "whole").

    Per cycle the steps that tell the readings apart are the Space payment's
    and the one that spans the transfer, so `cycles` cycles give 2*cycles - 1
    of them (the first cycle has no transfer before it). `corrupt` replaces the
    balance stated on those export rows (by index) with a figure no reading gives.
    """
    feed: list = []
    lines: list[str] = []
    main_balance = whole_balance = 1_000_000
    first = date(2026, 9, 1)
    for k in range(cycles):
        a = first + timedelta(days=3 * k)
        payment, space_payment, transfer = 1000 + 7 * k, 2000 + 11 * k, 5000 + 13 * k

        def row(account, minor, when, ident, desc, *, internal=False):
            return replace(
                pay(account, FEED, ident, minor, 1, desc, internal=internal),
                value_date=when,
                booking_date=when,
                content_key=content_key(amount_minor=minor, value_date=when, description=desc),
            )

        feed.append(row(MAIN, -payment, a, f"p{k}", f"Shop {k}"))
        feed.append(row(BILLS, -space_payment, a + timedelta(days=1), f"s{k}", f"Bill {k}"))
        feed.append(row(MAIN, -transfer, a + timedelta(days=2), f"o{k}", "To Bills", internal=True))
        feed.append(
            row(BILLS, transfer, a + timedelta(days=2), f"i{k}", "From Main", internal=True)
        )
        main_balance -= payment
        whole_balance -= payment
        shown_after_payment = main_balance if kind == "main" else whole_balance
        whole_balance -= space_payment
        shown_after_space = main_balance if kind == "main" else whole_balance
        main_balance -= transfer
        for index, (when, name, minor, shown) in enumerate(
            [
                (a, f"Shop {k}", -payment, shown_after_payment),
                (a + timedelta(days=1), f"Bill {k}", -space_payment, shown_after_space),
            ]
        ):
            position = 2 * k + index
            if position in corrupt:
                shown = 987_654
            lines.append(
                f"{when:%d/%m/%Y},{name},{name},FASTER PAYMENT,"
                f"{minor / 100:.2f},{shown / 100:.2f},"
            )
    return feed, lines


class TestHowManyBreaksTheVerdictSurvives:
    CYCLES = 20
    DISCRIMINATING = 2 * CYCLES - 1

    def build(self, store, tmp_path, kind, corrupt=frozenset()):
        home = Household(store, MAP)
        feed, lines = cycle_rows(kind, self.CYCLES, corrupt)
        home.arrive(*feed)
        import_export(store, tmp_path, lines)
        return home

    @pytest.mark.parametrize("kind", ["main", "whole"])
    def test_Export_WhenEveryFigureIsRight_FollowsEveryDiscriminatingStep(
        self, store, tmp_path, kind
    ):
        home = self.build(store, tmp_path, kind)

        meaning = meaning_of(home, CSV)

        right, wrong = (
            (meaning.main, meaning.whole) if kind == "main" else (meaning.whole, meaning.main)
        )
        assert meaning.verdict == kind
        assert meaning.steps == self.DISCRIMINATING
        assert (right, wrong) == (self.DISCRIMINATING, 0)

    def test_Export_WhenOneFigureIsCorrupt_IsStillReadAndTheResidueRemainsAsDifferences(
        self, store, tmp_path
    ):
        # One wrong figure breaks the step into it and the step out of it.
        home = self.build(store, tmp_path, "main", frozenset({17}))

        meaning = meaning_of(home, CSV)
        opening = opening_of(home)

        assert (meaning.verdict, meaning.main) == (
            READ_AS_MAIN, self.DISCRIMINATING - 2,
        )
        assert meaning.main >= READING_THRESHOLD * meaning.steps
        assert opening.differing, "the residue is kept, never dropped"

    def test_Export_WhenMoreFiguresAreCorruptThanTheThresholdAllows_IsUsedForNothing(
        self, store, tmp_path
    ):
        home = self.build(store, tmp_path, "main", frozenset({5, 17, 29}))

        meaning = meaning_of(home, CSV)

        assert meaning.verdict == NEITHER
        assert meaning.main < READING_THRESHOLD * meaning.steps
        assert opening_of(home).readings == ()

    def test_Export_WhenBothReadingsWouldFit_IsUsedForNothing(self, store, tmp_path, monkeypatch):
        import obdi.verify.balance_meaning as meaning_module

        monkeypatch.setattr(meaning_module, "READING_THRESHOLD", 0.0)
        home = self.build(store, tmp_path, "main")

        assert meaning_of(home, CSV).verdict == BOTH
        assert opening_of(home).readings == ()
