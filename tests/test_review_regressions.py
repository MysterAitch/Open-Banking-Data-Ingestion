"""Regressions for defects found by adversarial review.

Every test here failed before its fix. They are grouped separately from the
matching tests so the reason they exist stays visible: each one covers a case
the original tests did not imagine, and all four were silent - the suite passed
189/189 while money was being lost.

The common shape of the mistake: the original matching rules were designed
around ONE question, "is this the same payment seen through a different door?",
and were never asked the opposite question, "are these two different payments
that merely look alike?". A statement is full of the latter.
"""

from datetime import date

import pytest

from obdi.ingest import import_file, reconcile_batch
from obdi.matching import MatchTier, resolve, supersede
from obdi.models import SourceTier, Transaction, TransactionStatus
from obdi.store import Store


def txn(
    *,
    source: str,
    source_id: str | None = None,
    amount: int = -5000,
    day: int = 1,
    description: str = "STANDING ORDER",
    account: str = "halifax",
    status: TransactionStatus = TransactionStatus.BOOKED,
    entity_id: str = "",
    internal: bool = False,
) -> Transaction:
    from obdi.identity import content_key

    when = date(2026, 3, day)
    # A source supplying an id is authoritative by definition.
    tier = SourceTier.AUTHORITATIVE if source_id else SourceTier.SYNTHETIC
    return Transaction(
        account_id=account,
        amount_minor=amount,
        value_date=when,
        booking_date=when,
        description=description,
        source=source,
        source_id=source_id,
        tier=tier,
        status=status,
        entity_id=entity_id,
        is_internal_transfer=internal,
        content_key=content_key(
            account_id=account, amount_minor=amount, value_date=when, description=description
        ),
    )


class TestRepeatedPaymentsWithinOneSource:
    """The costliest defect: an id-less export losing money to fuzzy matching."""

    def test_Payments_WhenTwoSimilarOnesInOneIdlessExport_BothKept(self, tmp_path):
        # Two rows of ONE file can never be the same payment observed twice.
        qif = (
            b"!Type:Bank\n"
            b"D01/03/2026\nT-20.00\nPCASH WITHDRAWAL HIGH ST\n^\n"
            b"D05/03/2026\nT-20.00\nPCASH WITHDRAWAL STATION RD\n^\n"
        )
        path = tmp_path / "export.qif"
        path.write_bytes(qif)

        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, path, account_id="halifax")
            rows = store.transactions_for_account("halifax")

        assert len(rows) == 2
        assert sum(t.amount_minor for t in rows) == -4000

    def test_Payments_WhenWeeklyStandingOrderRepeats_EveryInstalmentKept(self, tmp_path):
        # Exactly 7 days apart, so it sat on the fuzzy window boundary and all
        # three collapsed into one.
        qif = (
            b"!Type:Bank\n"
            b"D01/03/2026\nT-50.00\nPSTANDING ORDER\n^\n"
            b"D08/03/2026\nT-50.00\nPSTANDING ORDER\n^\n"
            b"D15/03/2026\nT-50.00\nPSTANDING ORDER\n^\n"
        )
        path = tmp_path / "export.qif"
        path.write_bytes(qif)

        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, path, account_id="halifax")
            rows = store.transactions_for_account("halifax")

        assert len(rows) == 3
        assert sum(t.amount_minor for t in rows) == -15000

    def test_Payments_WhenSameSourceAndBothSettled_NotFuzzyMatched(self):
        stored = txn(source="qif", day=1)
        assert resolve(txn(source="qif", day=5), [stored]).tier is MatchTier.UNRESOLVED

    def test_Payment_WhenPendingSettlesWithinOneSource_StillMatched(self):
        # The one same-source case that IS the same payment twice, and which
        # the fix must not break.
        pending = txn(source="truelayer", source_id="tl-p", day=1, status=TransactionStatus.PENDING)
        settled = txn(source="truelayer", source_id="tl-b", day=3, description="STANDING ORDER LTD")
        assert resolve(settled, [pending]).existing is pending


class TestConflictingIdsWithinOneSource:
    """Tier 2 had the brake tier 3 had, and the two disagreed."""

    def test_Payments_WhenIdenticalButDifferentProviderIds_BothKept(self, tmp_path):
        first = txn(source="monzo-csv", source_id="tx-1", amount=-350, description="COFFEE SHOP")
        second = txn(source="monzo-csv", source_id="tx-2", amount=-350, description="COFFEE SHOP")

        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [first, second], digest="d1")
            rows = store.transactions_for_account("halifax")

        assert len(rows) == 2
        assert sum(t.amount_minor for t in rows) == -700

    def test_Payments_WhenSameSourceIdsDiffer_NotMatchedOnContentAlone(self):
        stored = txn(source="monzo-csv", source_id="tx-1")
        incoming = txn(source="monzo-csv", source_id="tx-2")
        assert resolve(incoming, [stored]).tier is MatchTier.UNRESOLVED

    def test_Payment_WhenSeenByTwoDifferentSources_StillMatchedOnContent(self):
        # The cross-source case must survive the fix.
        stored = txn(source="truelayer", source_id="tl-1")
        incoming = txn(source="qif", source_id=None)
        assert resolve(incoming, [stored]).tier is MatchTier.CONTENT_KEY


class TestSupersessionPreservesConfirmedFacts:
    def test_Transfer_WhenSupersededByLaterSighting_RemainsAnInternalTransfer(self):
        # Pairing is expensive to establish and was being thrown away by any
        # later pull, silently reclassifying transfers as spending.
        confirmed = txn(source="starling", entity_id="ent-1", internal=True)
        later = txn(source="starling", source_id="new-id", internal=False)
        assert supersede(confirmed, later).is_internal_transfer

    def test_Transfer_WhenLaterSightingIsInternalButStoredWasNot_BecomesInternal(self):
        stored = txn(source="starling", entity_id="ent-1", internal=False)
        later = txn(source="starling", source_id="new-id", internal=True)
        assert supersede(stored, later).is_internal_transfer

    def test_Transaction_WhenSuperseded_KeepsItsIdentity(self):
        stored = txn(source="starling", entity_id="ent-1")
        assert supersede(stored, txn(source="starling", source_id="x")).entity_id == "ent-1"


class TestUpsertKeepsProvenanceConsistent:
    def test_Transaction_WhenSupersededByAnotherSource_SourceAndIdStayInStep(self, tmp_path):
        # A row carrying one provider's id under another provider's name breaks
        # tier-one matching, which then duplicates on the next pull.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn(source="qif", source_id=None)], digest="d1")
            reconcile_batch(store, [txn(source="truelayer", source_id="tl-9")], digest="d2")

            rows = store.transactions_for_account("halifax")
            assert len(rows) == 1
            assert (rows[0].source, rows[0].source_id) == ("truelayer", "tl-9")

    def test_Transaction_WhenPulledAgainAfterSupersession_NotDuplicated(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn(source="qif", source_id=None)], digest="d1")
            reconcile_batch(store, [txn(source="truelayer", source_id="tl-9")], digest="d2")
            reconcile_batch(store, [txn(source="truelayer", source_id="tl-9")], digest="d3")

            assert len(store.transactions_for_account("halifax")) == 1


class TestRepeatedDownloadsOfOneSource:
    """Export caps force overlapping downloads, so this is the normal case."""

    def test_Export_WhenImportedTwice_TransactionsNotDuplicated(self, tmp_path):
        qif = b"!Type:Bank\nD01/03/2026\nT-20.00\nPTESCO\n^\nD02/03/2026\nT-35.00\nPSHELL\n^\n"
        path = tmp_path / "export.qif"
        path.write_bytes(qif)

        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, path, account_id="halifax")
            import_file(store, path, account_id="halifax")
            rows = store.transactions_for_account("halifax")

        assert len(rows) == 2
        assert sum(t.amount_minor for t in rows) == -5500

    def test_Export_WhenItRepeatsAPaymentAndIsReimported_CountsPreserved(self, tmp_path):
        # Two genuinely identical rows AND a re-import: the file must yield two
        # rows, and importing it twice must still yield two.
        qif = b"!Type:Bank\nD01/03/2026\nT-20.00\nPTESCO\n^\nD01/03/2026\nT-20.00\nPTESCO\n^\n"
        path = tmp_path / "export.qif"
        path.write_bytes(qif)

        with Store(tmp_path / "s.sqlite3") as store:
            import_file(store, path, account_id="halifax")
            import_file(store, path, account_id="halifax")
            rows = store.transactions_for_account("halifax")

        assert len(rows) == 2
        assert sum(t.amount_minor for t in rows) == -4000

    def test_Export_WhenOverlappingRangeDownloaded_OverlapNotDuplicated(self, tmp_path):
        first = b"!Type:Bank\nD01/03/2026\nT-20.00\nPTESCO\n^\nD05/03/2026\nT-31.00\nPBOOTS\n^\n"
        overlapping = (
            b"!Type:Bank\nD05/03/2026\nT-31.00\nPBOOTS\n^\nD09/03/2026\nT-42.00\nPARGOS\n^\n"
        )

        with Store(tmp_path / "s.sqlite3") as store:
            for name, payload in [("a.qif", first), ("b.qif", overlapping)]:
                path = tmp_path / name
                path.write_bytes(payload)
                import_file(store, path, account_id="halifax")
            rows = store.transactions_for_account("halifax")

        assert len(rows) == 3
        assert sum(t.amount_minor for t in rows) == -9300


class TestOneStoredTransactionClaimedOnce:
    def test_Payments_WhenTwoIncomingCouldMatchOneStored_OnlyOneClaimsIt(self, tmp_path):
        # A file recorded one of two similar payments; the API then reports
        # both. The stored row can only be one of them, so the other is new.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(store, [txn(source="qif", source_id=None, day=3)], digest="d1")
            reconcile_batch(
                store,
                [
                    txn(source="truelayer", source_id="tl-1", day=3),
                    txn(source="truelayer", source_id="tl-2", day=4),
                ],
                digest="d2",
            )

            rows = store.transactions_for_account("halifax")

        assert len(rows) == 2
        assert sum(t.amount_minor for t in rows) == -10000


@pytest.mark.parametrize("day_gap", [1, 5, 7])
class TestFuzzyWindowStillWorksAcrossSources:
    def test_Payment_WhenDatesDifferAcrossSources_StillMatched(self, day_gap):
        stored = txn(source="qif", source_id=None, day=1, description="TESCO")
        incoming = txn(
            source="truelayer", source_id="tl-1", day=1 + day_gap, description="TESCO STORES LTD"
        )
        assert resolve(incoming, [stored]).existing is stored
