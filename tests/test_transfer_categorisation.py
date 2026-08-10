"""Confirmed internal transfers stay uncategorised by default.

Money moving between your own accounts has not left the household, so
categorising either leg would double-count it against real spending (the
YNAB model, adopted as the starting position - a default to be revisited on
evidence, not a mandate). The consequence for every categorisation surface:
confirmed legs are excluded from the worklist, skipped by the rule sweep,
and invisible to propagation - and each exclusion is COUNTED, so the
default's cost stays observable and the evidence for revisiting it is
always on the table. A human may still annotate a leg directly (the ladder
is untouched); the machines just never do it for them.
"""

from __future__ import annotations

from datetime import date

from obdi.categorise import (
    apply_rules,
    propagation_proposals,
    uncategorised_summary,
)
from obdi.ingest import pair_transfers_across_store, reconcile_batch
from obdi.models import SourceTier, Transaction
from obdi.store import Store

CURRENT = "starling-personal"
SAVINGS = "starling-holiday-fund"


def txn(
    account: str,
    day: int,
    amount: int,
    desc: str,
    *,
    source_id: str,
) -> Transaction:
    return Transaction(
        account_id=account,
        amount_minor=amount,
        currency="GBP",
        value_date=date(2026, 3, day),
        booking_date=date(2026, 3, day),
        description=desc,
        source="starling",
        source_id=source_id,
        tier=SourceTier.AUTHORITATIVE,
    )


def _store_with_confirmed_pair(tmp_path) -> Store:
    """A confirmed transfer pair plus one ordinary purchase."""
    store = Store(tmp_path / "s.sqlite3")
    reconcile_batch(
        store,
        [
            txn(CURRENT, 14, -5000, "TO SAVINGS", source_id="out-1"),
            txn(CURRENT, 15, -450, "NETFLIX.COM", source_id="n-1"),
        ],
        digest="d1",
    )
    reconcile_batch(
        store,
        [txn(SAVINGS, 14, 5000, "FROM CURRENT", source_id="in-1")],
        digest="d2",
    )
    pair_transfers_across_store(store)
    return store


class TestTransfersStayUncategorised:
    def test_TheWorklist_LeavesConfirmedLegsOut_AndCountsThem(self, tmp_path):
        with _store_with_confirmed_pair(tmp_path) as store:
            worklist = uncategorised_summary(store)

            names = [name for name, _count, _example in worklist.groups]
            assert any("NETFLIX" in name for name in names)
            assert not any("SAVINGS" in name for name in names)
            assert not any("CURRENT" in name for name in names)
            assert worklist.transfer_legs == 2

    def test_TheSweep_SkipsConfirmedLegs_EvenWhenARuleMatches(self, tmp_path):
        with _store_with_confirmed_pair(tmp_path) as store:
            rules = {
                "payee_rules": [],
                "category_rules": [
                    {"match": "savings", "category": "Savings"},
                    {"match": "netflix", "category": "Subscriptions"},
                ],
            }

            summary = apply_rules(store, rules)

            assert summary.categorised == 1
            assert summary.transfer_legs == 2
            held = store.annotations("category")
            assert len(held) == 1
            (value, _provenance) = next(iter(held.values()))
            assert value == "Subscriptions"

    def test_Propagation_NeverProposesOntoAConfirmedLeg(self, tmp_path):
        # Two same-amount rows share a description group; only the
        # unconfirmed one is reachable by propagation.
        with Store(tmp_path / "s.sqlite3") as store:
            reconcile_batch(
                store,
                [
                    txn(CURRENT, 10, -5000, "MOVE 1", source_id="p-1"),
                    txn(CURRENT, 14, -5000, "MOVE 2", source_id="p-2"),
                    txn(CURRENT, 20, -5000, "MOVE 3", source_id="p-3"),
                ],
                digest="d1",
            )
            reconcile_batch(
                store,
                [txn(SAVINGS, 14, 5000, "MOVE IN", source_id="p-in")],
                digest="d2",
            )
            pair_transfers_across_store(store)
            confirmed = {
                t.source_id for t in store.all_transactions() if t.transfer_confirmed
            }
            assert "p-2" in confirmed, "fixture must confirm one leg"
            seed = next(
                t.entity_id
                for t in store.all_transactions()
                if t.source_id == "p-1"
            )
            store.annotate(seed, "category", "House fund", provenance="human")

            report = propagation_proposals(store)

            targets = [t for p in report.proposals for t in p.targets]
            reached = {
                t.source_id
                for t in store.all_transactions()
                if t.entity_id in targets
            }
            assert reached == {"p-3"}
            assert report.transfer_legs >= 2

    def test_AConfirmedLegWithAHumanAnnotation_DoesNotSeedPropagation(
        self, tmp_path
    ):
        # A human may annotate a leg directly - the ladder allows it - but
        # the machine does not generalise from a row the default says to
        # leave alone.
        with _store_with_confirmed_pair(tmp_path) as store:
            leg = next(
                t.entity_id for t in store.all_transactions() if t.transfer_confirmed
            )
            store.annotate(leg, "category", "Savings", provenance="human")

            report = propagation_proposals(store)

            assert all(not p.targets for p in report.proposals)
