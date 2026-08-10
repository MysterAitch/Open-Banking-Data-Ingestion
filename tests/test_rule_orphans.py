"""Annotations outlive the rules that made them - so say so.

A rule sweep only ever visits rows a rule MATCHES, so deleting a rule
strands its annotations: nothing revisits a row no rule claims any more,
and the category quietly persists as though someone still meant it.
Keeping the rule but emptying it cannot help either - a rule that produces
no value produces no write, and no write can undo a previous one.

The honest mechanism is therefore detection plus an explicit sweep-up: the
summary names how many annotations no current rule would produce, and
--prune deletes them. Rank still governs - pruning is a RULE retracting
rule-made work, so a human's or a model's annotation is never swept away by
a rules file edit.

The same detector covers a second case for free: a row that has since
become a confirmed transfer leg is skipped by the sweep, so any rule-made
category it picked up before pairing found its partner shows up as
orphaned too.
"""

from __future__ import annotations

from datetime import date
from typing import ClassVar

from obdi.categorise import apply_rules
from obdi.ingest import pair_transfers_across_store, reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store


def txn(
    day: int, amount: int, desc: str, *, source_id: str, account: str = "starling-personal"
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=amount,
        currency="GBP",
        value_date=date(2026, 1, day),
        booking_date=date(2026, 1, day),
        description=desc,
        source="starling",
        source_id=source_id,
        tier=SourceTier.AUTHORITATIVE,
    )


NETFLIX_AND_TESCO: dict[str, list[dict[str, str]]] = {
    "category_rules": [
        {"match": "netflix", "category": "Subscriptions"},
        {"match": "tesco", "category": "Groceries"},
    ]
}
NETFLIX_ONLY: dict[str, list[dict[str, str]]] = {
    "category_rules": [{"match": "netflix", "category": "Subscriptions"}]
}


def _seed(store: Store) -> None:
    reconcile_batch(
        store,
        [
            txn(5, -450, "NETFLIX.COM", source_id="n-1"),
            txn(6, -1230, "TESCO STORES 5223", source_id="t-1"),
        ],
        digest="d1",
    )


class TestARetiredRuleIsFlagged:
    def test_DeletingARule_LeavesItsAnnotations_AndTheSweepSaysSo(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            apply_rules(store, NETFLIX_AND_TESCO)

            summary = apply_rules(store, NETFLIX_ONLY)

            assert summary.orphans == 1
            assert "Groceries" in " ".join(summary.orphan_samples)
            assert len(store.annotations("category")) == 2, "reported, not deleted"

    def test_AnEmptiedRule_CannotUndoItsOwnWork(self, tmp_path):
        # The tempting fix - keep the rule but blank it - writes nothing, so
        # the old annotation survives exactly as before. Pinned so nobody
        # believes otherwise.
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            apply_rules(store, NETFLIX_AND_TESCO)

            emptied: dict[str, list[dict[str, str]]] = {
                "category_rules": [
                    {"match": "netflix", "category": "Subscriptions"},
                    {"match": "tesco", "category": ""},
                ]
            }
            apply_rules(store, emptied)

            held = store.annotations("category")
            tesco = next(
                t.entity_id
                for t in store.all_transactions()
                if "TESCO" in t.description
            )
            assert held[tesco][0] == "Groceries"

    def test_Pruning_RemovesThem_AndOnlyThem(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            apply_rules(store, NETFLIX_AND_TESCO)

            summary = apply_rules(store, NETFLIX_ONLY, prune=True)

            assert summary.pruned == 1
            held = store.annotations("category")
            assert len(held) == 1
            assert next(iter(held.values()))[0] == "Subscriptions"

    def test_APruneDryRun_CountsWithoutDeleting(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            apply_rules(store, NETFLIX_AND_TESCO)

            summary = apply_rules(store, NETFLIX_ONLY, prune=True, dry_run=True)

            assert summary.orphans == 1
            assert summary.pruned == 1
            assert len(store.annotations("category")) == 2

    def test_NoRulesRetired_MeansNoOrphans(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            apply_rules(store, NETFLIX_AND_TESCO)

            summary = apply_rules(store, NETFLIX_AND_TESCO)

            assert summary.orphans == 0


class TestRankStillGoverns:
    def test_HumanWork_IsNeverOrphanedNorPruned_ByARulesFileEdit(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            tesco = next(
                t.entity_id
                for t in store.all_transactions()
                if "TESCO" in t.description
            )
            store.annotate(tesco, "category", "Food shopping", provenance="human")

            summary = apply_rules(store, NETFLIX_ONLY, prune=True)

            assert summary.orphans == 0
            assert summary.pruned == 0
            assert store.annotations("category")[tesco] == ("Food shopping", "human")

    def test_PropagatedModelWork_SurvivesARulesFileEdit(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            tesco = next(
                t.entity_id
                for t in store.all_transactions()
                if "TESCO" in t.description
            )
            store.annotate(
                tesco, "category", "Groceries", provenance="model:propagation"
            )

            summary = apply_rules(store, NETFLIX_ONLY, prune=True)

            assert summary.pruned == 0
            assert store.annotations("category")[tesco][1] == "model:propagation"


class TestLaterConfirmedTransferLegs:
    def test_ALegCategorisedBeforePairing_IsOrphanedOnceConfirmed(self, tmp_path):
        # The boarded cleanup, covered by the same detector: a row gains a
        # category, later pairing proves it internal, and the sweep skips it
        # from then on - so the stale category must be findable.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [txn(5, -5000, "TO SAVINGS", source_id="out-1")],
                digest="d1",
            )
            rules: dict[str, list[dict[str, str]]] = {
                "category_rules": [{"match": "savings", "category": "Saving"}]
            }
            apply_rules(store, rules)

            reconcile_batch(
                store,
                [
                    txn(
                        5,
                        5000,
                        "FROM CURRENT",
                        source_id="in-1",
                        account="starling-holiday-fund",
                    )
                ],
                digest="d2",
            )
            pair_transfers_across_store(store)

            summary = apply_rules(store, rules, prune=True)

            assert summary.pruned == 1
            assert store.annotations("category") == {}


class TestPayeeRulesToo:
    RULES: ClassVar[dict[str, list[dict[str, str]]]] = {
        "payee_rules": [{"match": "TESCO", "payee": "Tesco"}]
    }

    def test_ARetiredPayeeRule_IsAlsoDetected(self, tmp_path):
        with Store(tmp_path / "s.sqlite3") as store:
            _seed(store)
            apply_rules(store, self.RULES)

            summary = apply_rules(store, {"payee_rules": []}, prune=True)

            assert summary.pruned == 1
            assert store.annotations("payee") == {}
