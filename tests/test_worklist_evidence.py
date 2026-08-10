"""The worklist must show strings a rule can actually match.

Group labels are lossy by design - digits and reference punctuation are
stripped so 'COSTA COFFEE 101' and 'COSTA COFFEE 202' group together. But a
person writes rules FROM the worklist, and a label is not a rule string: a
card descriptor like 'UBER *TRIP HELP.UBER.COM' displays as 'UBER TRIP',
whose substring appears nowhere in the raw description. The rule then
silently matches nothing, which reads exactly like a rule that matched
nothing because there was nothing to match.

So every group carries a real example, and the sweep reports per-rule hit
counts - a rule with zero hits names itself instead of hiding in a total.
"""

from __future__ import annotations

from datetime import date

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


class TestTheWorklistShowsMatchableEvidence:
    def test_EachGroup_CarriesARawExample_NotJustTheStrippedLabel(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(5, -1120, "UBER *TRIP HELP.UBER.COM", source_id="u-1"),
                    txn(6, -940, "UBER *TRIP HELP.UBER.COM", source_id="u-2"),
                ],
                digest="d1",
            )

            worklist = uncategorised_summary(store)

            label, count, example = worklist.groups[0]
            assert label == "UBER TRIP HELP.UBER.COM"
            assert count == 2
            assert example == "UBER *TRIP HELP.UBER.COM"
            assert "*" in example, "the example must be the unstripped string"

    def test_ARuleWrittenFromTheExample_Matches_WhereTheLabelWouldNot(
        self, tmp_path
    ):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [txn(5, -1120, "UBER *TRIP HELP.UBER.COM", source_id="u-1")],
                digest="d1",
            )
            worklist = uncategorised_summary(store)
            label, _count, example = worklist.groups[0]

            from_label = apply_rules(
                store,
                {"category_rules": [{"match": label, "category": "Transport"}]},
                dry_run=True,
            )
            from_example = apply_rules(
                store,
                {"category_rules": [{"match": example, "category": "Transport"}]},
                dry_run=True,
            )

            assert from_label.categorised == 0, "the label alone is unmatchable"
            assert from_example.categorised == 1


class TestPerRuleHitCounts:
    def test_TheSweep_ReportsHitsPerRule_SoADeadRuleNamesItself(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(5, -450, "NETFLIX.COM", source_id="n-1"),
                    txn(6, -1230, "TESCO STORES 5223", source_id="t-1"),
                    txn(7, -940, "UBER *TRIP", source_id="u-1"),
                ],
                digest="d1",
            )
            rules = {
                "category_rules": [
                    {"match": "netflix", "category": "Subscriptions"},
                    {"match": "tesco", "category": "Groceries"},
                    {"match": "uber trip", "category": "Transport"},
                ]
            }

            summary = apply_rules(store, rules, dry_run=True)

            assert summary.hits == {"netflix": 1, "tesco": 1, "uber trip": 0}
            assert summary.dead_rules() == ["uber trip"]

    def test_APayeeRule_ThatNeverFires_IsAlsoReported(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [txn(5, -450, "NETFLIX.COM", source_id="n-1")],
                digest="d1",
            )
            rules = {
                "payee_rules": [
                    {"match": "NETFLIX", "payee": "Netflix"},
                    {"match": "SPOTIFY", "payee": "Spotify"},
                ]
            }

            summary = apply_rules(store, rules, dry_run=True)

            assert summary.hits["NETFLIX"] == 1
            assert summary.dead_rules() == ["SPOTIFY"]
