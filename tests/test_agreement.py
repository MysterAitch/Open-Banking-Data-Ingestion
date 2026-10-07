"""How far an account is in agreement, and which rows are cleared, asserted on invented accounts.

Every answer below was fixed from the scenario's construction before the first run.

The account is `everyday` from test_balance_anchors. Its rows sum to, by the end of the day named:

    03-02  -1,250    03-05  +8,750    03-10  +6,750    03-15  +3,450    03-20  +3,950

A balance of 1,000.00 stated for the end of 03-05 therefore opens the account at
100,000 - 8,750 = 91,250, and the rows then predict, at the end of 03-10, 91,250 + 6,750 = 98,000
(980.00), at 03-15 94,700 (947.00), and at 03-20 95,200 (952.00).
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

import pytest

from obdi.agreement import (
    AGREES,
    DEFINES,
    HELD_CONFLICT,
    HELD_MOVEMENT,
    HELD_UNMET,
    MET,
    NONE,
    UNMET,
    UNTESTED,
    Fault,
    Known,
    derive_agreement,
    held_sentence,
    standing_line,
    standing_of,
)
from obdi.balance_anchors import STATED, STATEMENT, Anchor, derive_opening, record_stated_anchor
from obdi.clearing import cleared_by, cleared_entity_ids
from obdi.core.models import Transaction, TransactionStatus
from obdi.core.namespaces import CLEARING_SOURCES, SOURCES
from obdi.core.page_times import marks_removed
from obdi.ingest.store import Store
from obdi.ledger import build_ledger
from obdi.movement_completeness import MISSING, MovementCompleteness, RowCountFault
from round_up_corpus import rows_the_provider_makes
from test_balance_anchors import ACCOUNT, everyday
from test_ledger import land, txn

D = date


@pytest.fixture
def store(tmp_path):
    with Store(tmp_path / "agreement.sqlite3") as opened:
        yield opened


def state(store: Store, movement: MovementCompleteness | None = None):
    ledger = build_ledger(store, ACCOUNT, None, bound=False, movement=movement)
    assert ledger.standing is not None
    return ledger.standing.own


def stated(store: Store, *pairs: tuple[str, str]) -> None:
    for day, amount in pairs:
        record_stated_anchor(store, ACCOUNT, day, amount)


class TestAnAccountWithKnownBalances:
    def test_Agreement_WhenEveryKnownBalanceIsMet_RunsThroughTheLatest(self, store):
        everyday(store)
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"), ("2026-03-20", "952.00"))

        found = state(store, MovementCompleteness())

        assert found.state == AGREES
        assert found.through == D(2026, 3, 20)
        assert (found.known_from, found.known_to, found.known_count) == (
            D(2026, 3, 5), D(2026, 3, 20), 3,
        )
        assert found.tested_count == 2, "the first defines the opening and tests nothing"
        assert found.held is None

    def test_Agreement_WhenTheMiddleBalanceIsUnmet_StopsBeforeItEvenIfALaterOneIsMet(self, store):
        """03-15 is stated as 950.00 where the rows predict 947.00; 03-20 is right again."""
        everyday(store)
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"),
               ("2026-03-15", "950.00"), ("2026-03-20", "952.00"))

        found = state(store, MovementCompleteness())

        assert found.state == HELD_UNMET
        assert found.through == D(2026, 3, 10)
        assert found.held is not None and found.held.day == D(2026, 3, 15)
        assert held_sentence(found) == (
            "The transactions do not add up to the known balance for 2026-03-15 "
            "(a balance you stated)."
        )

    def test_Agreement_WhenAMovementFaultSitsBetweenTwoMetBalances_StopsBeforeTheFault(
        self, store
    ):
        """Every known balance is met, but a movement the source listed is not held, dated 03-12."""
        everyday(store)
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"), ("2026-03-20", "952.00"))
        report = MovementCompleteness(
            row_faults=[RowCountFault(ACCOUNT, "src-a", D(2026, 3, 12), "out", MISSING, 1, 0)]
        )

        found = state(store, report)

        assert found.state == HELD_MOVEMENT
        assert found.through == D(2026, 3, 10)
        assert found.held is not None and found.held.day == D(2026, 3, 12)
        assert (
            "A check of the money moved found a problem dated 2026-03-12, so the transactions "
            "cannot be shown to add up from then on"
        ) in (
            held_sentence(found)
        )

    def test_Agreement_WhenTheMovementFaultBelongsToAnotherAccount_IsNotHeldBackByIt(self, store):
        everyday(store)
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"))
        report = MovementCompleteness(
            row_faults=[RowCountFault("elsewhere", "src-a", D(2026, 3, 6), "out", MISSING, 1, 0)]
        )

        assert state(store, report).through == D(2026, 3, 10)

    def test_Agreement_WhenAMovementFaultIsDatedAfterTheLatestKnownBalance_HoldsNothingBack(
        self, store
    ):
        everyday(store)
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"))
        report = MovementCompleteness(
            row_faults=[RowCountFault(ACCOUNT, "src-a", D(2026, 3, 25), "out", MISSING, 1, 0)]
        )

        found = state(store, report)

        assert found.state == AGREES and found.through == D(2026, 3, 10) and found.held is None

    def test_Agreement_WhenNoKnownBalanceIsStated_SaysThereIsNothingToCheckAgainst(self, store):
        everyday(store)

        found = state(store, MovementCompleteness())

        assert found.state == NONE
        assert found.through is None and found.known_count == 0
        assert standing_line(found, None) == (
            "No known balance, so there is nothing to check the transactions against."
        )

    def test_Agreement_WhenOnlyOneKnownBalanceExists_IsUntestedAndVerifiesNothing(self, store):
        everyday(store)
        stated(store, ("2026-03-10", "1000.00"))

        found = state(store, MovementCompleteness())

        assert found.state == UNTESTED
        assert found.through is None
        assert held_sentence(found) == (
            "Only one known balance (2026-03-10); a second is needed before the transactions "
            "can be checked."
        )

    def test_Agreement_WhenMovementWasNotRead_SaysSo(self, store):
        everyday(store)
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"))

        found = state(store)

        assert found.movement_checked is False
        assert found.through == D(2026, 3, 10)

    def test_StandingLine_WhenBalancesAreMetAndNothingIsProtected_NamesTheThreeDates(self, store):
        everyday(store)
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"))

        found = state(store, MovementCompleteness())

        assert marks_removed(standing_line(found, None)) == (
            "The transactions add up to every known balance from 2026-03-05 to 2026-03-10"
            " (6 days)."
        )
        assert standing_line(found, D(2026, 3, 10)).endswith("protected through 2026-03-10.")


class TestKnownBalancesThatDisagreeWithEachOther:
    ROWS: ClassVar[list[Transaction]] = [
        txn(ACCOUNT, "src-a", "x1", D(2026, 3, 2), -1250, "ONE"),
        txn(ACCOUNT, "src-a", "x2", D(2026, 3, 5), 10000, "TWO"),
    ]

    def opening(self, *anchors: Anchor):
        return derive_opening(ACCOUNT, anchors, self.ROWS)

    def test_Agreement_WhenTwoSourcesStateDifferentFiguresForOneDay_IsAConflictNotAFaultInTheRows(
        self,
    ):
        opening = self.opening(
            Anchor(D(2026, 3, 5), 100000, STATED),
            Anchor(D(2026, 3, 5), 100500, STATEMENT, "halifax-statement-pdf"),
            Anchor(D(2026, 3, 20), 100000, STATED),
        )

        found = standing_of(opening, [ACCOUNT], MovementCompleteness()).own

        assert found.state == HELD_CONFLICT
        assert found.through is None
        assert [c.day for c in found.conflicts] == [D(2026, 3, 5)]
        sentence = held_sentence(found)
        assert "Two sources state different balances for 2026-03-05" in sentence
        assert "so nothing after that day can be checked" in sentence
        assert "not a fault in the transactions" in sentence
        assert "halifax-statement-pdf" in sentence

    def test_Agreement_WhenTwoSourcesStateTheSameFigureForOneDay_IsNotAConflict(self):
        opening = self.opening(
            Anchor(D(2026, 3, 5), 100000, STATED),
            Anchor(D(2026, 3, 5), 100000, STATEMENT, "halifax-statement-pdf"),
        )

        found = standing_of(opening, [ACCOUNT], MovementCompleteness()).own

        assert found.conflicts == ()

    def test_Derive_WhenABankBalanceForAMomentDiffersFromADayEndFigure_IsNotAConflict(self):
        found = derive_agreement(
            [
                Known(D(2026, 3, 5), "stated", DEFINES, 100),
                Known(D(2026, 3, 10), "statement", MET, 200),
                Known(D(2026, 3, 10), "starling", MET, 250, instant=True),
            ],
            [],
        )

        assert found.conflicts == () and found.through == D(2026, 3, 10)

    def test_Derive_WhenTheFirstKnownBalanceIsUnmetAndNothingElseIsKnown_HasNoThrough(self):
        found = derive_agreement(
            [Known(D(2026, 3, 5), "a", DEFINES, 1), Known(D(2026, 3, 9), "a", UNMET, 2)], []
        )

        assert found.state == HELD_UNMET and found.through is None

    def test_Derive_WhenAConflictAndAFaultBeginOnTheSameDay_SaysTheConflict(self):
        found = derive_agreement(
            [
                Known(D(2026, 3, 5), "a", DEFINES, 1),
                Known(D(2026, 3, 9), "a", MET, 1),
                Known(D(2026, 3, 9), "b", MET, 2),
                Known(D(2026, 3, 12), "a", MET, 1),
            ],
            [Fault(D(2026, 3, 9), "a fault")],
        )

        assert found.held is not None and found.held.kind == HELD_CONFLICT


class TestThePage:
    def page(self, store, *, unmasked: bool = False, movement=None) -> str:
        from obdi.web_ledger import render_ledger

        ledger = build_ledger(
            store, ACCOUNT, "2026-03", bound=False,
            movement=movement if movement is not None else MovementCompleteness(),
        )
        return render_ledger(ledger, unmasked=unmasked).decode("utf-8")

    @staticmethod
    def words(page: str) -> str:
        """The page's words: no tags (a date is set in a span that never breaks), entities read."""
        import html
        import re

        return html.unescape(re.sub(r"<[^>]+>", "", page))

    def test_Page_WhenBalancesAreMet_SaysWhatAddsUpToWhichKnownBalances(self, store):
        everyday(store)
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"))

        page = self.page(store)

        assert "Adds up to the known balances to 2026-03-10." in self.words(page)
        assert 'class="trust bad"' not in page and 'class="trust none"' not in page

    def test_Page_WhenNoBalanceIsKnown_SaysThereIsNothingToCheckAgainst(self, store):
        everyday(store)

        page = self.page(store)

        assert 'class="trust none"' in page
        assert "Nothing to check against." in self.words(page)

    def test_Page_WhenABalanceIsUnmet_NamesWhatHoldsBackAndLinksItsExplanation(self, store):
        everyday(store)
        stated(
            store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"), ("2026-03-15", "950.00")
        )

        page = self.page(store)

        assert (
            "The transactions do not add up to the known balance for 2026-03-15"
            in self.words(page)
        )
        assert 'href="#opening"' in page, "the explanation is on this page"
        assert "<details><summary>Known balances (" in page, (
            "a fold the thing to do opens at the explanation, and not one that opens itself"
        )
        assert '<div id="opening">' in page

    def test_Page_WhenAMovementFaultHoldsAgreementBack_LinksTheMovementChecks(self, store):
        everyday(store)
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"))
        report = MovementCompleteness(
            row_faults=[RowCountFault(ACCOUNT, "src-a", D(2026, 3, 8), "out", MISSING, 1, 0)]
        )

        page = self.page(store, movement=report)

        assert (
            "A check of the money moved found a problem dated 2026-03-08, so the transactions "
            "cannot be shown to add up from then on"
        ) in self.words(page)
        assert 'href="/identity-health"' in page

    def test_Page_ShowsWhichSourceClearedARowAndTheCounts(self, store):
        land(store, "d-csv", txn(ACCOUNT, "starling-csv", "c1", D(2026, 3, 2), -100, "ONE"))
        land(store, "d-agg", txn(ACCOUNT, "truelayer-booked", "t2", D(2026, 3, 3), -200, "TWO"))

        page = self.page(store)

        assert "cleared by <code>starling-csv</code>" in page
        assert page.count(">cleared by ") == 1, "the aggregator-only row carries no mark"
        assert "1 transaction is cleared and 1 is not" in page

    def test_Page_WhenMasked_NeverShowsAStatedFigureOrTheOpening(self, store):
        everyday(store)
        stated(store, ("2026-03-05", "1000.00"), ("2026-03-10", "980.00"), ("2026-03-15", "950.00"))

        page = self.page(store)

        for figure in ("1000.00", "1,000.00", "100000", "980.00", "98000", "950.00", "91250"):
            assert figure not in page, figure


class TestClearing:
    def test_Registry_EverySourceIsEitherAListingOrDeclaredNotToClear(self):
        assert CLEARING_SOURCES <= SOURCES
        for relay in ("truelayer", "truelayer-booked", "truelayer-card-booked", "manual"):
            assert relay not in CLEARING_SOURCES
        for lister in ("starling", "starling-csv", "halifax-statement-pdf", "qif"):
            assert lister in CLEARING_SOURCES

    def test_ClearedBy_WhenOnlyTheAggregatorListsTheRow_IsNotCleared(self):
        assert cleared_by(["truelayer-booked"], "booked") == ()

    def test_ClearedBy_WhenAStatementListsAPendingRow_IsNotCleared(self):
        assert cleared_by(["halifax-statement-pdf"], "pending") == ()

    def test_ClearedBy_WhenTheAggregatorAndAnExportBothListTheRow_NamesOnlyTheExport(self):
        assert cleared_by(["truelayer-booked", "starling-csv"], "booked") == ("starling-csv",)

    def test_Ledger_CountsClearedAndUnclearedRowsPerMonthAndPerAccount(self, store):
        """March: c1 (export, cleared), c2 (aggregator only), c3 (aggregator and export, cleared),
        c4 (export but pending), c5 (export but void: history, in neither count).
        April: c6 (the bank's feed, cleared)."""
        land(store, "d-agg", txn(ACCOUNT, "truelayer-booked", "t2", D(2026, 3, 3), -200, "TWO"),
             txn(ACCOUNT, "truelayer-booked", "t3", D(2026, 3, 4), -300, "THREE"))
        land(
            store,
            "d-csv",
            txn(ACCOUNT, "starling-csv", "c1", D(2026, 3, 2), -100, "ONE"),
            txn(ACCOUNT, "starling-csv", "c3", D(2026, 3, 4), -300, "THREE"),
            txn(ACCOUNT, "starling-csv", "c4", D(2026, 3, 5), -400, "FOUR PENDING",
                status=TransactionStatus.PENDING),
            txn(ACCOUNT, "starling-csv", "c5", D(2026, 3, 6), -500, "FIVE VOID",
                status=TransactionStatus.VOID),
        )
        land(store, "d-feed", *rows_the_provider_makes(ACCOUNT, "f6", "SIX", 600, "2026-04-02"))

        march = build_ledger(store, ACCOUNT, "2026-03", bound=False)

        assert march.clearing is not None
        assert (march.clearing.cleared, march.clearing.uncleared) == (3, 2)
        assert [(m.month, m.cleared, m.uncleared) for m in march.clearing.months] == [
            ("2026-03", 2, 2),
            ("2026-04", 1, 0),
        ]
        assert march.summary is not None
        assert (march.summary.cleared, march.summary.uncleared) == (2, 2)
        by_description = {row.description: row.cleared_by for row in march.rows}
        assert by_description["ONE"] == ("starling-csv",)
        assert by_description["TWO"] == ()
        assert by_description["THREE"] == ("starling-csv",)
        assert by_description["FOUR PENDING"] == ()
        assert len(cleared_entity_ids(store)) == 3
