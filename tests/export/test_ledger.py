"""What an account's ledger knows, asserted on the data before any page shows it.

The expected counts below were fixed from the scenario's construction BEFORE the
first run, and each scenario states how they were reached. The store is built
through `reconcile_batch`, the door a pull and an import both use, so the
sightings, pairings and review flags are the ones the application writes.

The privacy rule that shapes the design - values masked unless asked for - is
tested on the rendered page (test_ledger_page.py). What is tested here is that
the DATA carries every structural fact a masked page needs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date

import pytest

from obdi.core.masking import (
    MASKED_TOTAL,
    Disclosed,
    Structural,
    mask_text,
    structural_field_names,
)
from obdi.core.models import SourceTier, Transaction, TransactionStatus
from obdi.export.replay import (
    ActualAccountBinding,
    build_payload,
    withheld_reason,
)
from obdi.ingest.accounts import AccountRecord, AccountRef
from obdi.ingest.identity import content_key
from obdi.ingest.pipeline import pair_transfers_across_store, reconcile_batch
from obdi.ingest.store import Store
from obdi.read.ledger import (
    ANCHOR_QUERIES,
    QUERIES_PER_PAGE,
    STATEMENT_CHECK_QUERIES,
    AnchorLine,
    Ledger,
    LedgerRequestError,
    LedgerRow,
    Money,
    MonthSummary,
    OpeningView,
    Position,
    build_ledger,
)

#: Chosen so that no figure derived from them can appear by coincidence.
PRIVATE_MINOR = 73913
PRIVATE_PAYEE = "Zebra Crossing Cafe"
PRIVATE_REFERENCE = "QUAGGA-REFERENCE"
PRIVATE_CATEGORY = "Takeaway Zebras"

CURRENT = "current-account"
SAVINGS = "savings-account"


def txn(
    account: str,
    source: str,
    source_id: str,
    day: date,
    amount: int,
    description: str,
    *,
    status: TransactionStatus = TransactionStatus.BOOKED,
    internal: bool = False,
    counterparty: str = "",
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=amount,
        value_date=day,
        booking_date=day,
        description=description,
        counterparty=counterparty,
        source=source,
        source_id=source_id,
        content_key=content_key(amount_minor=amount, value_date=day, description=description),
        tier=SourceTier.AUTHORITATIVE,
        status=status,
        is_internal_transfer=internal,
    )


def land(store: Store, digest: str, *items: Transaction) -> None:
    reconcile_batch(store, list(items), digest=digest)


def row_id(store: Store, source_id: str) -> str:
    return next(
        t.entity_id for t in store.all_transactions() if t.source_id == source_id
    )


def build_household(store: Store) -> None:
    """The account every scenario below reads, with its counts fixed up front.

    CURRENT, fed by two sources, in March 2026:
      a-coffee   03-02  -1,250   seen by src-a AND src-b
      a-zebra    03-05  -73,913  src-a only; its payee, reference and category are private
      b-salary   03-09  +250,000 src-b only
      a-parking  03-12  -987     src-a only, PENDING
      a-vanished 03-15  -4,321   src-a only, VOID
      a-save     03-20  -5,000   src-a only, internal transfer, paired with SAVINGS
      a-claim    03-25  -3,000   src-a only, internal transfer claimed, no other side
      a-review   03-28  -1,111   src-a only, carries an open review flag
    February holds one row and May one, so April is an empty month between them.
    January holds two rows planted onto one identity. Their amounts differ by a
    penny because equal amounts on one date make the matcher open a review flag,
    which this scenario wants confined to a-review.
    """
    d = date
    land(
        store,
        "digest-a-1",
        txn(CURRENT, "src-a", "a-feb", d(2026, 2, 10), -2000, "FEB ITEM"),
        txn(CURRENT, "src-a", "a-coffee", d(2026, 3, 2), -1250, "COFFEE QUAGGA CAFE"),
        txn(
            CURRENT, "src-a", "a-zebra", d(2026, 3, 5), -PRIVATE_MINOR,
            f"ZEBRA {PRIVATE_REFERENCE}", counterparty=PRIVATE_PAYEE,
        ),
        txn(
            CURRENT, "src-a", "a-parking", d(2026, 3, 12), -987, "PENDING PARKING",
            status=TransactionStatus.PENDING,
        ),
        txn(
            CURRENT, "src-a", "a-vanished", d(2026, 3, 15), -4321, "VANISHED PENDING",
            status=TransactionStatus.VOID,
        ),
        txn(CURRENT, "src-a", "a-save", d(2026, 3, 20), -5000, "TO SAVINGS", internal=True),
        txn(CURRENT, "src-a", "a-claim", d(2026, 3, 25), -3000, "TO SOMEWHERE", internal=True),
        txn(CURRENT, "src-a", "a-review", d(2026, 3, 28), -1111, "REVIEWED PAYMENT"),
        txn(CURRENT, "src-a", "a-twin-1", d(2026, 1, 10), -100, "TWIN ONE"),
        txn(CURRENT, "src-a", "a-twin-2", d(2026, 1, 10), -101, "TWIN TWO"),
    )
    land(
        store,
        "digest-b-1",
        txn(CURRENT, "src-b", "b-coffee", d(2026, 3, 2), -1250, "COFFEE QUAGGA CAFE"),
        txn(CURRENT, "src-b", "b-salary", d(2026, 3, 9), 250000, "SALARY ZEBRA LTD"),
        txn(CURRENT, "src-b", "b-may", d(2026, 5, 3), -700, "MAY ITEM"),
    )
    land(
        store,
        "digest-a-2",
        txn(SAVINGS, "src-a", "s-save", d(2026, 3, 20), 5000, "FROM CURRENT"),
    )
    pair_transfers_across_store(store)
    store.queue_for_review(row_id(store, "a-review"), "kept apart by the same-source rule")
    store.connection.commit()
    store.annotate(
        row_id(store, "a-zebra"), "category", PRIVATE_CATEGORY, provenance="human"
    )
    store.annotate(row_id(store, "a-zebra"), "payee", PRIVATE_PAYEE, provenance="rule:sweep")
    # The row's id is also reported by src-a under a second provider id, which is
    # what a payment folded into it looks like from the sightings.
    zebra = next(t for t in store.all_transactions() if t.source_id == "a-zebra")
    store.record_source(replace(zebra, source_id="a-lost", artefact_digest="digest-lost"))
    store.connection.commit()
    # The state the door prevents: two rows on one identity.
    store.connection.execute(
        "UPDATE transactions SET content_key = 'shared-key', occurrence = 0 "
        "WHERE description IN ('TWIN ONE', 'TWIN TWO')"
    )
    store.connection.commit()


@pytest.fixture
def household(tmp_path):
    path = tmp_path / "household.sqlite3"
    with Store(path) as store:
        build_household(store)
    return path


def ledger_of(path, month: str | None = "2026-03", *, bound: bool = True, ref: str = CURRENT):
    with Store(path) as store:
        return build_ledger(store, ref, month, bound=bound, label="Household current")


def rows_by_description(ledger: Ledger) -> dict[str, LedgerRow]:
    return {row.description: row for row in ledger.rows}


class TestAnAccountFedByTwoSources:
    def test_Month_ListsEveryRowOnceNewestFirst(self, household):
        ledger = ledger_of(household)

        assert ledger.state == "ok"
        assert len(ledger.rows) == 8
        assert [row.dated for row in ledger.rows] == sorted(
            (row.dated for row in ledger.rows), reverse=True
        )

    def test_Sources_NameEveryPipeThatSightedARow_NotTheLastWriter(self, household):
        rows = rows_by_description(ledger_of(household))

        assert rows["COFFEE QUAGGA CAFE"].sources == ("src-a", "src-b")
        assert rows["SALARY ZEBRA LTD"].sources == ("src-b",)
        assert rows[f"ZEBRA {PRIVATE_REFERENCE}"].sources == ("src-a",)

    def test_Summary_CountsRowsPerSourceAndHowManyTwoSourcesSaw(self, household):
        summary = ledger_of(household).summary

        assert summary is not None
        assert summary.rows == 8
        assert dict(summary.per_source) == {"src-a": 7, "src-b": 2}
        assert summary.multi_source == 1

    def test_OneSourceFlag_MarksRowsOnlyOneOfTwoFeedingSourcesSaw(self, household):
        ledger = ledger_of(household)
        rows = rows_by_description(ledger)

        assert ledger.sources == ("src-a", "src-b")
        assert rows["COFFEE QUAGGA CAFE"].one_source is False
        assert rows["SALARY ZEBRA LTD"].one_source is True
        assert ledger.summary is not None
        assert ledger.summary.one_source == 7

    def test_OneSourceFlag_WhenOnlyOneSourceFeedsTheAccount_IsNeverRaised(self, household):
        ledger = ledger_of(household, "2026-03", ref=SAVINGS)

        assert ledger.sources == ("src-a",)
        assert [row.one_source for row in ledger.rows] == [False]

    def test_DatesDiffer_WhenTwoSourcesDatedOneRowDifferently_IsReported(self, tmp_path):
        """Predicted: the matcher merges a payment two sources dated a day apart,
        so one row carries two observed dates. If it does not merge them this
        fails and the prediction was wrong."""
        path = tmp_path / "drift.sqlite3"
        with Store(path) as store:
            coffee = "COFFEE QUAGGA CAFE"
            land(store, "da", txn("drift", "src-a", "x1", date(2026, 3, 2), -1250, coffee))
            land(store, "db", txn("drift", "src-b", "y1", date(2026, 3, 3), -1250, coffee))
        ledger = ledger_of(path, "2026-03", ref="drift")

        assert len(ledger.rows) == 1
        row = ledger.rows[0]
        assert row.sources == ("src-a", "src-b")
        assert row.dates_differ is True
        assert dict(row.observed) == {"src-a": "2026-03-02", "src-b": "2026-03-03"}

    def test_DatesDiffer_WhenSourcesAgree_IsNotRaised(self, household):
        rows = rows_by_description(ledger_of(household))

        assert rows["COFFEE QUAGGA CAFE"].dates_differ is False
        assert dict(rows["COFFEE QUAGGA CAFE"].observed) == {
            "src-a": "2026-03-02",
            "src-b": "2026-03-02",
        }


class TestStatusAndTransfersAndReview:
    def test_Summary_CountsPendingVoidTransfersAndReviews(self, household):
        summary = ledger_of(household).summary

        assert summary is not None
        assert summary.pending == 1
        assert summary.void == 1
        assert summary.transfers_confirmed == 1
        assert summary.transfers_claimed == 1
        assert summary.review_open == 1

    def test_Void_IsListedWithItsStatus_NotHidden(self, household):
        row = rows_by_description(ledger_of(household))["VANISHED PENDING"]

        assert row.status == "void"
        assert row.withheld == "void"

    def test_ConfirmedTransfer_NamesTheOtherAccount(self, household):
        row = rows_by_description(ledger_of(household))["TO SAVINGS"]

        assert row.transfer == "confirmed"
        assert row.transfer_other_account == SAVINGS

    def test_ConfirmedTransfer_IsSeenFromTheOtherAccountToo(self, household):
        row = ledger_of(household, "2026-03", ref=SAVINGS).rows[0]

        assert row.transfer == "confirmed"
        assert row.transfer_other_account == CURRENT

    def test_ClaimedTransfer_WithNoOtherSideHeld_IsClaimedNotConfirmed(self, household):
        row = rows_by_description(ledger_of(household))["TO SOMEWHERE"]

        assert row.transfer == "claimed"
        assert row.transfer_other_account == ""

    def test_OrdinaryRows_CarryNoTransferFlag(self, household):
        rows = rows_by_description(ledger_of(household))

        assert rows["SALARY ZEBRA LTD"].transfer == ""
        assert rows["COFFEE QUAGGA CAFE"].transfer_other_account == ""

    def test_Review_OnlyTheFlaggedRowCarriesIt(self, household):
        rows = rows_by_description(ledger_of(household))

        assert rows["REVIEWED PAYMENT"].review_open is True
        assert rows["REVIEWED PAYMENT"].review_reason
        assert rows["SALARY ZEBRA LTD"].review_open is False
        assert rows["SALARY ZEBRA LTD"].review_reason == ""

    def test_Review_WhenResolved_NoLongerFlagsTheRow(self, household):
        with Store(household) as store:
            store.resolve_review(row_id(store, "a-review"))
        rows = rows_by_description(ledger_of(household))

        assert rows["REVIEWED PAYMENT"].review_open is False

    def test_Pending_RowKeepsItsStatusAndIsStillSent(self, household):
        row = rows_by_description(ledger_of(household))["PENDING PARKING"]

        assert row.status == "pending"
        assert row.withheld == ""


class TestWhatActualWouldBeSent:
    def test_BoundAccount_WithholdsOnlyTheVoidRow_AndSendsBothKindsOfTransfer(
        self, household
    ):
        """Eight rows in March, one of them void.
        The confirmed transfer and the unpaired claim are sent like any other
        row: leaving them out put the account's balance in Actual out by their
        sum."""
        ledger = ledger_of(household, bound=True)
        summary = ledger.summary
        rows = rows_by_description(ledger)

        assert summary is not None
        assert summary.would_send == 7
        assert summary.withheld == 1
        assert dict(summary.withheld_by_reason) == {"void": 1}
        assert rows["TO SAVINGS"].withheld == ""
        assert rows["TO SOMEWHERE"].withheld == ""

    def test_UnboundAccount_WithholdsEverything_NamingTheMissingBinding(self, household):
        summary = ledger_of(household, bound=False).summary

        assert summary is not None
        assert summary.would_send == 0
        assert summary.withheld == 8
        assert dict(summary.withheld_by_reason) == {
            "void": 1,
            "no Actual binding": 7,
        }

    def test_Rule_AgreesWithThePayloadBuilderForEveryRow(self, household):
        """The ledger and the push must not be able to disagree about a row."""
        bindings = [ActualAccountBinding(CURRENT, "actual-1")]
        with Store(household) as store:
            transactions = store.all_transactions()
            sent = {
                item["imported_id"]
                for items in build_payload(transactions, bindings).values()
                for item in items
            }
            for t in transactions:
                bound = t.account_id == CURRENT
                imported = f"{t.content_key}:{t.occurrence}"
                assert (withheld_reason(t, bound=bound) is None) == (imported in sent), t

    def test_Sums_ForABoundAccountHoldingTransfers_AgreeBecauseTransfersAreSent(
        self, household
    ):
        summary = ledger_of(household, bound=True).summary

        assert summary is not None
        # Non-void rows: 250000 - (1250 + 73913 + 987 + 5000 + 3000 + 1111) = 164739
        assert summary.store_sum == Money(164739, "GBP")
        # The two transfers (8000) travel too, so what is sent is the same figure.
        assert summary.sent_sum == Money(164739, "GBP")
        assert summary.store_direction == "in"
        assert summary.sums_differ is False

    def test_Sums_WhenNothingIsWithheld_Agree(self, tmp_path):
        path = tmp_path / "plain.sqlite3"
        with Store(path) as store:
            land(
                store, "d1",
                txn("plain", "src-a", "p1", date(2026, 3, 1), -500, "ONE"),
                txn("plain", "src-a", "p2", date(2026, 3, 2), 900, "TWO"),
            )
        ledger = ledger_of(path, ref="plain", bound=True)

        assert ledger.summary is not None
        assert ledger.summary.sums_differ is False
        assert ledger.summary.store_sum == ledger.summary.sent_sum == Money(400, "GBP")
        assert ledger.position is not None
        assert ledger.position.differs is False

    def test_Sums_ForAnUnboundAccount_Differ(self, household):
        summary = ledger_of(household, bound=False).summary

        assert summary is not None
        assert summary.sent_sum == Money(0, "GBP")
        assert summary.sums_differ is True

    def test_Position_SumsEveryNonVoidRowUpToTheEndOfTheMonth(self, household):
        position = ledger_of(household, "2026-03", bound=True).position

        assert isinstance(position, Position)
        assert position.through == "2026-03-31"
        # March 164739, February -2000, the two January twins -100 and -101.
        assert position.store_balance == Money(162538, "GBP")
        assert position.sent_balance == Money(162538, "GBP")
        assert position.differs is False
        # 7 non-void March rows + 1 February + 2 January.
        assert position.rows_counted == 10

    def test_Position_ExcludesLaterMonths(self, household):
        earlier = ledger_of(household, "2026-02").position
        later = ledger_of(household, "2026-05").position

        assert earlier is not None and later is not None
        assert earlier.store_balance == Money(-2201, "GBP")
        assert earlier.store_direction == "out"
        assert later.store_balance == Money(162538 - 700, "GBP")

    def test_UnsendableRow_IsCountedApartFromSentAndWithheld(self, tmp_path):
        """A row the push builder refuses fails the whole push, so the ledger
        must not count it as sent."""
        path = tmp_path / "euro.sqlite3"
        with Store(path) as store:
            land(
                store, "d1",
                txn("euro", "src-a", "e1", date(2026, 3, 1), -500, "ONE"),
                replace(
                    txn("euro", "src-a", "e2", date(2026, 3, 2), -700, "TWO"),
                    currency="EUR",
                ),
            )
        summary = ledger_of(path, ref="euro", bound=True).summary

        assert summary is not None
        assert (summary.would_send, summary.withheld, summary.unsendable) == (1, 0, 1)
        assert summary.mixed_currency is True
        assert summary.sent_sum.minor == -500


class TestMonthNavigation:
    def test_DefaultMonth_IsTheNewestMonthWithRows(self, household):
        ledger = ledger_of(household, None)

        assert ledger.month == "2026-05"
        assert ledger.newest_month == "2026-05"
        assert ledger.oldest_month == "2026-01"

    def test_EmptyMonth_SaysSo_AndStillOffersItsNeighbours(self, household):
        ledger = ledger_of(household, "2026-04")

        assert ledger.state == "empty-month"
        assert ledger.rows == ()
        assert ledger.summary is None
        assert (ledger.previous_month, ledger.next_month) == ("2026-03", "2026-05")

    def test_EmptyMonth_StillCarriesTheRunningPosition(self, household):
        position = ledger_of(household, "2026-04").position

        assert position is not None
        assert position.store_balance == Money(162538, "GBP")

    def test_Neighbours_CrossTheYearBoundary(self, household):
        """The household holds rows from 2026-01 to 2026-05."""
        assert ledger_of(household, "2025-12").next_month == "2026-01"
        assert ledger_of(household, "2027-01").previous_month == "2026-12"

    def test_Neighbours_AreNotOfferedBeyondTheMonthsThatHoldRows(self, household):
        """Stepping past either end led to a page announcing an empty month,
        which reads as a gap in the data and is only the calendar running on."""
        assert ledger_of(household, "2026-01").previous_month == ""
        assert ledger_of(household, "2026-05").next_month == ""
        assert ledger_of(household, "2026-05").previous_month == "2026-04"
        assert ledger_of(household, "2026-01").next_month == "2026-02"

    @pytest.mark.parametrize("bad", ["2026-13", "2026-00", "March", "2026-3", "20260", "0000-01"])
    def test_Month_WhenNotAMonth_IsRefused(self, household, bad):
        with pytest.raises(LedgerRequestError):
            ledger_of(household, bad)


class TestUnknownAndEmptyAccounts:
    def test_UnknownAccount_IsNotAnEmptyHealthyOne(self, household):
        ledger = ledger_of(household, ref="no-such-account")

        assert ledger.state == "unknown"
        assert ledger.rows == () and ledger.summary is None

    def test_DeclaredAccountHoldingNothing_IsDistinguishedFromUnknown(self, household):
        with Store(household) as store:
            store.declare_account(AccountRecord(ref=AccountRef("empty-isa"), label="Empty ISA"))
        ledger = ledger_of(household, ref="empty-isa")

        assert ledger.state == "no-rows"


class TestIdentityFlags:
    def test_SharesIdentity_OnlyTheTwoRowsOnOneIdentityCarryIt(self, household):
        january = ledger_of(household, "2026-01")
        march = ledger_of(household, "2026-03")

        assert [row.shares_identity for row in january.rows] == [True, True]
        assert not any(row.shares_identity for row in march.rows)

    def test_Absorbed_OnlyTheRowReportedUnderTwoIdsCarriesIt(self, household):
        rows = rows_by_description(ledger_of(household))

        assert rows[f"ZEBRA {PRIVATE_REFERENCE}"].absorbed_ids == 2
        assert rows["COFFEE QUAGGA CAFE"].absorbed_ids == 0
        assert rows["SALARY ZEBRA LTD"].absorbed_ids == 0


class TestAnnotations:
    def test_Annotated_RowCarriesCategoryPayeeAndWhoSetThem(self, household):
        row = rows_by_description(ledger_of(household))[f"ZEBRA {PRIVATE_REFERENCE}"]

        assert row.category == PRIVATE_CATEGORY
        assert row.payee == PRIVATE_PAYEE
        assert row.annotated_by == "human,rule"

    def test_Unannotated_RowCarriesNone(self, household):
        row = rows_by_description(ledger_of(household))["SALARY ZEBRA LTD"]

        assert (row.category, row.payee, row.annotated_by) == ("", "", "")


class TestWhatAPageCosts:
    def _statements(self, path, ref) -> int:
        issued: list[str] = []
        with Store(path) as store:
            # The statement checks of the store are worked out once and held, so a page is
            # measured with them held, as the second page of a process is.
            build_ledger(store, ref, "2026-03", bound=True)
            store.connection.set_trace_callback(issued.append)
            build_ledger(store, ref, "2026-03", bound=True)
            store.connection.set_trace_callback(None)
        return sum(1 for sql in issued if sql.lstrip().upper().startswith("SELECT"))

    def test_Page_CostsTheDocumentedNumberOfStatements(self, household):
        # An account with no TrueLayer records and no held statements: the
        # anchor search has no per-artefact reads to add.
        assert (
            self._statements(household, CURRENT)
            == QUERIES_PER_PAGE + ANCHOR_QUERIES + STATEMENT_CHECK_QUERIES
        )

    def test_Page_CostsTheSameForAMonthTenTimesAsBig(self, tmp_path):
        path = tmp_path / "big.sqlite3"
        with Store(path) as store:
            land(
                store, "dbig",
                *(
                    txn(
                        CURRENT, "src-a", f"big-{n}", date(2026, 3, 1 + n % 28),
                        -(n + 1), f"ITEM {n}",
                    )
                    for n in range(120)
                ),
            )
        assert (
            self._statements(path, CURRENT)
            == QUERIES_PER_PAGE + ANCHOR_QUERIES + STATEMENT_CHECK_QUERIES
        )


class TestStructureIsDeclaredNotAssumed:
    """A field added to a record is masked until somebody says it is structure.

    These list what is structural today, so adding a field forces a decision
    in a diff rather than passing unnoticed.
    """

    def test_Row_ValuesAreExactlyTheFieldsCarryingTheMoneyAndWords(self):
        from dataclasses import fields

        values = {f.name for f in fields(LedgerRow)} - structural_field_names(LedgerRow)
        assert values == {
            "description", "counterparty", "amount", "review_reason",
            "send_refusal", "category", "payee", "balance_after",
        }

    def test_Summary_OnlyTheSumsAreValues(self):
        from dataclasses import fields

        values = {f.name for f in fields(MonthSummary)} - structural_field_names(MonthSummary)
        assert values == {"store_sum", "sent_sum"}

    def test_Position_OnlyTheBalancesAreValues(self):
        from dataclasses import fields

        values = {f.name for f in fields(Position)} - structural_field_names(Position)
        assert values == {"store_balance", "sent_balance"}

    def test_AnchorLine_OnlyTheBalanceAndTheDifferenceAreValues(self):
        from dataclasses import fields

        values = {f.name for f in fields(AnchorLine)} - structural_field_names(AnchorLine)
        assert values == {"balance", "difference"}

    def test_OpeningView_OnlyTheOpeningFigureIsAValue(self):
        from dataclasses import fields

        values = {f.name for f in fields(OpeningView)} - structural_field_names(OpeningView)
        assert values == {"opening"}

    def test_Ledger_TheOpeningIsAStructuralRecordWhoseOwnFieldsDecideWhatIsShown(self):
        """The record is reached through the ledger's view, so its figures are
        masked by its own declarations and not by the ledger's."""
        from dataclasses import fields

        values = {f.name for f in fields(Ledger)} - structural_field_names(Ledger)
        assert values == set()

    def test_Opening_MaskedThroughTheLedgersView_ShowsNoDigitOfAnyFigure(self, tmp_path):
        from obdi.verify.balance_anchors import record_stated_anchor

        path = tmp_path / "masked-opening.sqlite3"
        with Store(path) as store:
            land(store, "d", txn("acct", "src-a", "x", date(2026, 3, 2), -1250, "ONE"))
            record_stated_anchor(store, "acct", "2026-03-10", "4517.89")
            record_stated_anchor(store, "acct", "2026-03-20", "4000.00")
        ledger = ledger_of(path, "2026-03", ref="acct")

        masked = Disclosed(ledger, unmasked=False).opening
        shown = Disclosed(ledger, unmasked=True).opening

        assert [line.balance for line in masked.anchors] == [MASKED_TOTAL, MASKED_TOTAL]
        assert masked.opening == MASKED_TOTAL
        assert [line.verdict for line in masked.anchors] == ["", "differs"]
        assert [line.balance for line in shown.anchors] == ["£4,517.89", "£4,000.00"]
        assert shown.opening == "£4,530.39"

    def test_AFieldAddedWithoutADeclaration_IsMaskedByDefault(self):
        @dataclass(frozen=True)
        class Later:
            shown: Structural[str]
            added_later: str

        view = Disclosed(Later(shown="visible", added_later="Secret 42"), unmasked=False)

        assert view.shown == "visible"
        assert view.added_later == "Xxxxxx 99"

    def test_AValueField_ReadsInFullOnlyWhenUnmaskedIsAskedFor(self):
        @dataclass(frozen=True)
        class Later:
            added_later: str

        assert Disclosed(Later("Secret 42"), unmasked=True).added_later == "Secret 42"

    def test_NestedRecords_AreDisclosedThroughTheSameView(self, household):
        view = Disclosed(ledger_of(household), unmasked=False)

        assert view.rows[0].dated
        assert PRIVATE_PAYEE not in " ".join(row.payee for row in view.rows)
        assert view.summary.store_sum == MASKED_TOTAL

    def test_Masked_EverySumAndBalance_HidesItsSize_WhileAPaymentKeepsItsShape(
        self, household
    ):
        """A payment's length says little; a balance's says how much there is."""
        view = Disclosed(ledger_of(household), unmasked=False)

        assert view.summary.store_sum == MASKED_TOTAL
        assert view.summary.sent_sum == MASKED_TOTAL
        assert view.position.store_balance == MASKED_TOTAL
        assert view.position.sent_balance == MASKED_TOTAL
        amounts = {row.amount for row in view.rows}
        assert MASKED_TOTAL not in amounts
        assert all(re.fullmatch(r"£9[9,]*\.99", amount) for amount in amounts)


class TestMasking:
    def test_Text_KeepsLengthCasePunctuationAndLosesTheValue(self):
        assert mask_text("Zebra Crossing Cafe") == "Xxxxx Xxxxxxxx Xxxx"
        assert mask_text("QUAGGA-REFERENCE") == "XXXXXX-XXXXXXXXX"
        assert mask_text("£1,234.56") == "£9,999.99"

    def test_Text_ReplacesSymbolsThatCouldIdentifyAPayee(self):
        assert mask_text("Cafe \U0001f984") == "Xxxx ?"

    def test_Money_ShowsMagnitudeAndCurrencySymbolOnly(self):
        assert str(Money(-73913, "GBP")) == "£739.13"
        assert str(Money(5, "GBP")) == "£0.05"
        assert str(Money(123456789, "GBP")) == "£1,234,567.89"
        assert str(Money(-700, "JPY")) == "7.00"
