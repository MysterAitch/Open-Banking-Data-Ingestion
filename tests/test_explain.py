"""Identifying an opaque reference: the evidence a human needs.

Some descriptions name a merchant. Others - 'DAP90481679', 'T14262451',
'SPWCU   16353' - name nothing, yet repeat for years. A person CAN identify
those, but not from the string: they identify them from the shape of the
payments (monthly, same day, same amount, one account, always outbound).
This is that shape, rendered, so the answer to "what is this?" comes from
evidence rather than a guess.

The worklist's reference-code warning splits the same way: rows joined only
by what the stripping removed deserve "a rule here would guess", while a
reference that repeats deserves "identify it once, then rule on the exact
string" - opposite advice, so conflating them misleads.
"""

from __future__ import annotations

from datetime import date

from obdi.categorise import explain, uncategorised_summary
from obdi.ingest import reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store


def txn(
    month: int, day: int, amount: int, desc: str, *, source_id: str
) -> Transaction:
    return Transaction(
        account_id="starling-personal",
        amount_minor=amount,
        currency="GBP",
        value_date=date(2026, month, day),
        booking_date=date(2026, month, day),
        description=desc,
        source="starling",
        source_id=source_id,
        tier=SourceTier.AUTHORITATIVE,
    )


class TestExplainingAnOpaqueReference:
    def test_ARepeatingMonthlyDebit_ShowsItsCadenceAmountAndSpan(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, 15, -45000, "DAP90481679", source_id="d-1"),
                    txn(2, 15, -45000, "DAP90481679", source_id="d-2"),
                    txn(3, 15, -45000, "DAP90481679", source_id="d-3"),
                    txn(4, 15, -45000, "DAP90481679", source_id="d-4"),
                ],
                digest="d1",
            )

            found = explain(store, "DAP")

            assert found.count == 4
            assert found.outgoing == 4
            assert found.incoming == 0
            assert found.first == "2026-01-15"
            assert found.last == "2026-04-15"
            assert found.cadence() == "monthly"
            assert found.day_of_month == [15]
            assert found.distinct_descriptions == ["DAP90481679"]
            text = found.describe()
            assert "monthly" in text
            assert "450.00" in text

    def test_AnIrregularMixture_SaysSoRatherThanClaimingACadence(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, 3, -1000, "SHOP A", source_id="s-1"),
                    txn(1, 9, -2500, "SHOP A", source_id="s-2"),
                    txn(3, 27, -700, "SHOP A", source_id="s-3"),
                ],
                digest="d1",
            )

            found = explain(store, "SHOP A")

            assert found.count == 3
            assert "monthly" not in found.describe()
            assert found.amount_low == -2500
            assert found.amount_high == -700

    def test_NothingMatching_ReportsHonestlyInsteadOfEmptyOutput(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store, [txn(1, 3, -1000, "SHOP A", source_id="s-1")], digest="d1"
            )

            found = explain(store, "NOTHING LIKE THIS")

            assert found.count == 0
            assert "no transaction" in found.describe().lower()


class TestTheReferenceWarningSplitsByRepetition:
    def test_ARepeatingReference_AdvisesIdentifyThenRule(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, 15, -45000, "DAP90481679", source_id="d-1"),
                    txn(2, 15, -45000, "DAP90481679", source_id="d-2"),
                    txn(3, 15, -45000, "DAP90481679", source_id="d-3"),
                ],
                digest="d1",
            )

            group = uncategorised_summary(store).groups[0]

            assert group.reference_coded
            assert group.repeating, "3 rows, 1 string - a rule can be exact"

    def test_OneOffReferences_AdviseAgainstARule(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(1, 5, -100, "DAP90481679", source_id="d-1"),
                    txn(2, 6, -200, "DAP90481680", source_id="d-2"),
                    txn(3, 7, -300, "DAP90481681", source_id="d-3"),
                ],
                digest="d1",
            )

            group = uncategorised_summary(store).groups[0]

            assert group.reference_coded
            assert not group.repeating, "every row its own string"
