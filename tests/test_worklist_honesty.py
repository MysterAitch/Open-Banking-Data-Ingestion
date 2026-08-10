"""The worklist must not invent patterns, and the sweep must not report a
delta as a total.

Two defects the first live rules file exposed. (1) Digit-stripping groups
'COSTA COFFEE 101/202/303' usefully, but it also collapses bank reference
codes - 'DAP90481679', 'T14262451', 'DD14006323' - into tiny labels whose
rows share nothing but a prefix. Presenting those as the biggest groups
invites a rule that is pure guesswork. (2) A re-run reports only the rows
it CHANGED, which reads as the total categorised and understates coverage
the moment any sweep has run before.

Both fixes are the same principle: a count without its denominator, or
without the evidence behind it, forces forensic reconstruction.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

from obdi.categorise import apply_rules, uncategorised_summary
from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store


def txn(day: int, amount: int, desc: str, *, source_id: str) -> Transaction:
    return Transaction(
        account_id="starling-personal",
        amount_minor=amount,
        currency="GBP",
        value_date=date(2026, 1, day),
        booking_date=date(2026, 1, day),
        description=desc,
        source="starling",
        source_id=source_id,
        tier=SourceTier.AUTHORITATIVE,
    )


class TestReferenceCodedGroupsAreMarked:
    def test_AGroupOfBankReferences_IsFlagged_WithItsDistinctCount(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(5, -100, "DAP90481679", source_id="d-1"),
                    txn(6, -200, "DAP90481680", source_id="d-2"),
                    txn(7, -300, "DAP90481681", source_id="d-3"),
                ],
                digest="d1",
            )

            worklist = uncategorised_summary(store)

            group = worklist.groups[0]
            assert group.label == "DAP"
            assert group.count == 3
            assert group.distinct == 3, "every row a different string"
            assert group.reference_coded, "mostly digits - not a merchant"

    def test_ARealMerchantWithStoreCodes_IsNotFlagged(self, tmp_path):
        # The case digit-stripping exists to serve must survive the fix.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, -100, "COSTA COFFEE 101", source_id="c-1"),
                    txn(2, -110, "COSTA COFFEE 202", source_id="c-2"),
                    txn(3, -120, "COSTA COFFEE 303", source_id="c-3"),
                ],
                digest="d1",
            )

            group = uncategorised_summary(store).groups[0]

            assert group.label == "COSTA COFFEE"
            assert group.distinct == 3
            assert not group.reference_coded

    def test_ARepeatingMerchant_ReportsFewerDistinctStringsThanRows(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, -100, "NETFLIX.COM", source_id="n-1"),
                    txn(2, -100, "NETFLIX.COM", source_id="n-2"),
                    txn(3, -100, "NETFLIX.COM", source_id="n-3"),
                ],
                digest="d1",
            )

            group = uncategorised_summary(store).groups[0]

            assert (group.count, group.distinct) == (3, 1)
            assert not group.reference_coded


class TestTheSweepReportsCoverageNotJustItsDelta:
    RULES: ClassVar[dict[str, list[dict[str, str]]]] = {
        "category_rules": [{"match": "netflix", "category": "Subscriptions"}]
    }

    def test_ASecondRun_ReportsWhatAlreadyAgreed_AndTotalCoverage(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, -450, "NETFLIX.COM", source_id="n-1"),
                    txn(2, -450, "NETFLIX.COM", source_id="n-2"),
                    txn(3, -999, "MYSTERY", source_id="m-1"),
                ],
                digest="d1",
            )

            first = apply_rules(store, self.RULES)
            second = apply_rules(store, self.RULES)

            assert (first.categorised, first.agreed) == (2, 0)
            assert (second.categorised, second.agreed) == (0, 2), (
                "a re-run changes nothing but must not report zero coverage"
            )
            assert second.eligible == 3
            assert second.now_categorised == 2
            assert "2 of 3" in second.describe()

    def test_ADryRun_PredictsCoverage_WithoutWriting(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, -450, "NETFLIX.COM", source_id="n-1"),
                    txn(2, -999, "MYSTERY", source_id="m-1"),
                ],
                digest="d1",
            )

            summary = apply_rules(store, self.RULES, dry_run=True)

            assert summary.now_categorised == 1
            assert store.annotations("category") == {}
